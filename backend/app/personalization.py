"""Builds the personalized recommendations for one shopper.

Inputs are the Databricks next-basket model's scores and the shopper's
replenishment profile from the daily dump, plus live state the dump can't
know about: orders placed since, subscriptions, preferences the shopper just
changed, on-hand units at the fulfilling center, and their store credit. The
result is written to `recs::<shopper_id>`, which Sync Gateway replicates to
that shopper's phone - so Home and the Cart render from Couchbase Lite, even
offline, and re-rank themselves on the next sync after every order.
"""
from __future__ import annotations

from . import catalog as C
from . import pricing as P
from .docs import days_since, days_until, now_iso, recs_id, shopper_channel, shopper_id_doc
from .store import store


def _short(name: str) -> str:
    return name.split(",")[0]


def _reason(sku: str, s: dict, sug: dict) -> str:
    rep = s.get("replenishment", {}).get(sku, {})
    parts = []
    dso = days_since(rep.get("last_ordered"))
    dtr = (s["ml_scores"].get("days_to_runout") or {}).get(sku)
    if dtr is not None and dtr <= 7:
        parts.append("Likely out already" if dtr <= 0 else f"Runs out in about {dtr} day{'s' if dtr != 1 else ''}")
    elif dso is not None:
        parts.append(f"Last bought {dso} days ago")
    if rep.get("avg_monthly_qty"):
        parts.append(f"you go through ~{rep['avg_monthly_qty']:g} a month")
    if sug.get("reason") and len(parts) < 2:
        parts.append(sug["reason"])
    return " · ".join(parts[:2])


async def compute_recommendations(s: dict) -> dict:
    from .actions import get_inventory, shopper_offers, wallet_view  # avoid import cycle

    m = s["ml_scores"]
    inv, _ = await get_inventory(s["delivery"]["fulfillment_center"])
    tier = s["membership"]["tier"]
    # Prices here are the rules engine's as of now; the phone re-prices from the same rules on every render.
    pricing = await P.get(s["delivery"]["fulfillment_center"])
    hour = P.hour_now(await P.get_signals())
    offers = shopper_offers(s)

    def price(sku, qty=1):
        return P.quote(tier, {sku: qty}, pricing, hour, "", offers)[0]["unit_price"]

    prefs = s.get("preferences", {})
    favorite, avoid = set(prefs.get("favorite_categories", [])), set(prefs.get("avoid_categories", []))
    store_id = (s.get("home_store") or {}).get("store_id")
    subscribed = {x["sku"] for x in s.get("subscriptions", [])}
    recent = {l["sku"] for o in s.get("order_history", [])[:2] for l in o.get("lines", [])
              if days_since(o.get("date")) is not None and days_since(o.get("date")) <= 3}
    bought_30d = {l["sku"] for o in s.get("order_history", []) for l in o.get("lines", [])
                  if (days_since(o.get("date")) or 0) <= 30}

    lines = []
    for sug in m.get("replenish_lines", []):
        sku = sug["sku"]
        p = C.PRODUCT_BY_SKU.get(sku)
        if not p or sku in recent or sku in subscribed or p["category"] in avoid:
            continue  # just bought it, already on Subscribe & Save, or hidden by the shopper
        qty = int(sug["qty"])
        unit = price(sku, qty)
        on_hand = inv["on_hand"].get(sku, 0)
        affinity = m.get(C.CATEGORIES[p["category"]]["affinity"], 0.5)
        score = float(sug.get("confidence", 0.5)) * (0.6 + 0.4 * affinity)
        dtr = (m.get("days_to_runout") or {}).get(sku)
        if dtr is not None and dtr <= 7:
            score *= 1.2
        if p["category"] in favorite:
            score *= 1.15
        lines.append({
            "sku": sku, "name": p["name"], "category": p["category"], "category_name": C.CATEGORIES[p["category"]]["name"],
            "uom": p["uom"], "pack": p["pack"], "qty": qty, "list_price": p["list_price"], "unit_price": unit,
            "line_total": round(unit * qty, 2), "confidence": sug.get("confidence"), "reason": _reason(sku, s, sug),
            "on_hand_at_fc": on_hand, "availability": "in_stock" if on_hand >= qty else ("low" if on_hand > 0 else "backorder"),
            "days_to_runout": dtr, "aisle": C.aisle_for(store_id, sku) if store_id in C.STORES else None,
            "score": round(score, 3), "selected": score >= 0.45,
        })
    lines.sort(key=lambda l: -l["score"])
    for i, l in enumerate(lines):
        l["rank"] = i + 1

    for_you = []
    for rec in m.get("recommended", []):
        sku = rec["sku"]
        p = C.PRODUCT_BY_SKU.get(sku)
        if not p or p["category"] in avoid or sku in bought_30d or any(l["sku"] == sku for l in lines):
            continue
        unit = price(sku)
        affinity = m.get(C.CATEGORIES[p["category"]]["affinity"], 0.5)
        score = float(rec.get("confidence", 0.5)) * (0.6 + 0.4 * affinity) * (1.25 if p["category"] in favorite else 1.0)
        for_you.append({"sku": sku, "name": p["name"], "category": p["category"], "category_name": C.CATEGORIES[p["category"]]["name"],
                        "uom": p["uom"], "pack": p["pack"], "qty": 1, "list_price": p["list_price"], "unit_price": unit,
                        "line_total": unit, "reason": rec.get("reason") or "Shoppers like you buy it",
                        "confidence": rec.get("confidence"), "on_hand_at_fc": inv["on_hand"].get(sku, 0),
                        "limit_per_household": p.get("limit_per_household"),
                        "aisle": C.aisle_for(store_id, sku) if store_id in C.STORES else None, "score": round(score, 3)})
    # What the shopper asked for more of comes first, then the model's ranking.
    for_you.sort(key=lambda r: (r["category"] not in favorite, -r["score"]))

    price_matches = []
    for w in m.get("price_watch", []):
        p = C.PRODUCT_BY_SKU.get(w["sku"])
        if not p or w.get("competitor") not in C.COMPETITORS or w["sku"] in bought_30d:
            continue
        unit = price(w["sku"])
        if float(w["competitor_price_usd"]) < unit:
            price_matches.append({"sku": w["sku"], "name": p["name"], "competitor": w["competitor"],
                                  "competitor_price_usd": float(w["competitor_price_usd"]), "member_price_usd": unit,
                                  "saving_usd": round(unit - float(w["competitor_price_usd"]), 2), "seen_on": w.get("seen_on")})

    selected = [l for l in lines if l["selected"]]
    subtotal = round(sum(l["line_total"] for l in selected), 2)
    return {
        "lines": lines, "for_you": for_you[:6], "price_matches": price_matches,
        # Tailored promotions from loyalty data. Offers on something the shopper has hidden are dropped.
        "offers": [o for o in offers if o.get("category") not in avoid
                   and (not o.get("sku") or C.PRODUCT_BY_SKU.get(o["sku"], {}).get("category") not in avoid)],
        "pricing_version": pricing["version"],
        "suggested_total_usd": subtotal,
        "suggested_savings_usd": round(sum((l["list_price"] - l["unit_price"]) * l["qty"] for l in selected), 2),
        "wallet": wallet_view(s),
        "insights": insights(s, lines, price_matches, offers, P.active_deals(pricing, hour)),
    }


def insights(s: dict, lines: list[dict], price_matches: list[dict], offers: list | None = None,
             deals: list | None = None) -> list[str]:
    """The 'for you today' notes on the Home screen."""
    pts = []
    urgent = [l for l in lines if l.get("days_to_runout") is not None and l["days_to_runout"] <= 7]
    if urgent:
        pts.append("About to run out: " + ", ".join(_short(l["name"]) for l in urgent[:3]))
    short = [l for l in lines if l["selected"] and l["on_hand_at_fc"] < l["qty"]]
    if short:
        left = short[0]["on_hand_at_fc"]
        pts.append(f"{'Only ' + str(left) + ' left of' if left else 'Sold out for now:'} {_short(short[0]['name'])} at your fulfillment "
                   "center - part of your order may backorder")
    if price_matches:
        pm = price_matches[0]
        pts.append(f"{pm['competitor']} has {_short(pm['name'])} for ${pm['competitor_price_usd']:,.2f} - ask for a price match and save "
                   f"${pm['saving_usd']:,.2f}")
    if offers:
        o = offers[0]
        pts.append(f"Offer for you: {o['title']}. {o['reason']}")
    mine = {l["sku"] for l in lines}
    deal = next((d for d in deals or [] if d["kind"] == "overstock" and d["sku"] in mine), None)
    if deal and len(pts) < 4:
        pts.append(f"{_short(deal['name'])} is {deal['pct_off']}% off today: overstock markdown")
    w = s["wallet"]
    if float(w.get("store_credit_usd", 0)) > 0 and len(pts) < 4:
        pts.append(f"${float(w['store_credit_usd']):,.2f} store credit comes off your next order automatically")
    last = s["order_history"][0] if s.get("order_history") else None
    if last and len(pts) < 4:
        pts.append(f"Last order {last['order_id']} on {last['date']}: {last.get('line_count', len(last.get('lines', [])))} items, "
                   f"${last['total_usd']:,.2f}")
    ren = days_until(s["membership"].get("renewal_date"))
    if ren is not None and 0 <= ren <= 45 and s["membership"]["tier"] != "MEMBER" and len(pts) < 4:
        pts.append(f"{s['membership']['tier_name']} membership renews {s['membership']['renewal_date']}")
    if s.get("subscriptions") and len(pts) < 4:
        n = len(s["subscriptions"])
        pts.append(f"{n} Subscribe & Save item{'s' if n > 1 else ''} arriving on schedule")
    return pts[:4]


async def refresh_recommendations(shopper_id: str) -> dict | None:
    s = await store.get("shoppers", shopper_id_doc(shopper_id))
    if not s:
        return None
    recs = await compute_recommendations(s)
    doc = {
        "type": "recommendations", "shopper_id": shopper_id, "first_name": s["first_name"],
        **recs,
        "model_version": s["ml_scores"].get("model_version"), "segment": s["ml_scores"].get("segment"),
        "export_date": s.get("export_date"), "shopper_rev": s.get("rev", 0),
        "generated_at": now_iso(), "channels": [shopper_channel(shopper_id)],
    }
    await store.upsert("recommendations", recs_id(shopper_id), doc)
    return doc
