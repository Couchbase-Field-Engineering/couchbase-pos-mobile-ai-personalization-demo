"""Shopping actions - the business logic behind every MCP tool.

These functions are what actually changes state (orders, inventory, store
credit, subscriptions, returns, preferences). Prices come from the dynamic
pricing engine (`pricing.py`): member tier, multi-buy, inventory- and
hour-driven promotions, competitor matches, bundles and the shopper's own
offers. They are only ever reached
through `tool_gateway`, which routes them through the Couchbase Agent
Operations Manager when it is configured (RBAC, approvals, tracing, audit) and
calls them in-process otherwise. Every mutation bumps the shopper's `rev`,
which is what invalidates that shopper's context cache and re-ranks their
recommendations.

Every tool takes the `shopper_id` it acts for. The worker fills it from the
order document, which Sync Gateway only accepted from that signed-in shopper,
and the assistant fills it from the app token; a shopper can only ever act on
their own profile.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from . import catalog as C
from . import metrics
from . import pricing as P
from .docs import days_since, inventory_id, money, now_iso, shopper_id_doc, today
from .store import store


class ToolError(Exception):
    pass


def _h(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def short(name: str) -> str:
    """Product name without the pack-size tail, for sentences."""
    return name.split(",")[0]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
async def load_shopper(shopper_id: str) -> dict:
    s = await store.get("shoppers", shopper_id_doc(shopper_id))
    if not s:
        raise ToolError(f"No shopper '{shopper_id}'")
    return s


async def save_shopper(s: dict):
    s["rev"] = int(s.get("rev", 0)) + 1
    s["updated_at"] = now_iso()
    await store.upsert("shoppers", shopper_id_doc(s["shopper_id"]), s)
    from .loader import mark_live  # late import: loader -> personalization -> actions
    await mark_live(s["shopper_id"])


async def after_change(shopper_id: str):
    # late imports: personalization/assistant import this module
    from . import assistant, personalization
    await personalization.refresh_recommendations(shopper_id)
    await assistant.invalidate_context(shopper_id)


async def get_inventory(fc: str) -> tuple[dict, int]:
    doc, cas = await store.get_with_cas("inventory", inventory_id(fc))
    if doc is None:
        await store.insert("inventory", inventory_id(fc),
                           {"type": "inventory", "fc": fc, "fc_name": C.FULFILLMENT_CENTERS[fc]["name"],
                            "on_hand": C.initial_inventory(fc), "created_at": now_iso()})
        doc, cas = await store.get_with_cas("inventory", inventory_id(fc))
    return doc, cas


async def allocate_inventory(fc: str, wants: dict[str, int]) -> dict[str, int]:
    """Take what is on hand for each SKU (CAS-protected). Returns what was
    actually allocated per SKU; the rest is backordered."""
    for _ in range(10):
        doc, cas = await get_inventory(fc)
        got = {}
        for sku, qty in wants.items():
            avail = max(0, int(doc["on_hand"].get(sku, 0)))
            got[sku] = min(avail, int(qty))
            doc["on_hand"][sku] = avail - got[sku]
        doc["updated_at"] = now_iso()
        if await store.replace_cas("inventory", inventory_id(fc), doc, cas):
            return got
    raise ToolError("Inventory is busy - please try again")


def shopper_offers(s: dict) -> list[dict]:
    """The personal offers the nightly model issued that are still usable:
    not expired, and not already redeemed if they are one-time."""
    used = set(s.get("redeemed_offers", []))
    return [o for o in s["ml_scores"].get("offers", [])
            if o.get("offer_id") not in used and (not o.get("expires") or o["expires"] >= today().isoformat())]


def _merge(lines) -> dict[str, int]:
    if not isinstance(lines, list) or not lines:
        raise ToolError("An order needs at least one item")
    cart: dict[str, int] = {}
    for ln in lines:
        if not isinstance(ln, dict) or not ln.get("sku"):
            raise ToolError("Each line needs a sku and qty")
        p = C.PRODUCT_BY_SKU.get(ln["sku"])
        if not p:
            raise ToolError(f"Unknown item '{ln['sku']}'")
        cart[ln["sku"]] = cart.get(ln["sku"], 0) + int(ln.get("qty") or 1)
    for sku, qty in cart.items():
        if qty < 1 or qty > 24:
            raise ToolError(f"Quantity for {short(C.PRODUCT_BY_SKU[sku]['name'])} must be between 1 and 24")
    return cart


async def _price(s: dict, lines, option: str = "", match: dict | None = None, pricing_version: str = "",
                 priced_hour: int | None = None) -> tuple[list[dict], dict, int]:
    """Price lines for a shopper with the current rules - or, when a phone
    names the rule version and hour it priced the cart at, with exactly
    those (the price lock that makes an offline cart's total stick)."""
    cart = _merge(lines)
    pricing = await P.get(s["delivery"]["fulfillment_center"], pricing_version or None)
    hour = int(priced_hour) % 24 if priced_hour is not None and str(priced_hour) != "" else P.hour_now(await P.get_signals())
    return P.quote(s["membership"]["tier"], cart, pricing, hour, option, shopper_offers(s), match), pricing, hour


def _option(s: dict, delivery_option: str) -> str:
    option = delivery_option or s["delivery"].get("default_option") or C.DELIVERY_OPTIONS[1]
    if option not in C.DELIVERY_OPTIONS:
        raise ToolError(f"Delivery option must be one of: {', '.join(C.DELIVERY_OPTIONS)}")
    return option


def _totals(s: dict, lines: list[dict], option: str) -> dict:
    """Same arithmetic the phone runs offline (mobile/src/pricing.ts)."""
    tier = C.TIER_BY_CODE[s["membership"]["tier"]]
    subtotal = round(sum(l["line_total"] for l in lines), 2)
    fee = 0.0 if option == C.PICKUP_OPTION or subtotal >= tier["free_delivery_min_usd"] else C.DELIVERY_FEE_USD
    tax = round(subtotal * C.TAX_RATE, 2)
    total = round(subtotal + fee + tax, 2)
    credit = round(min(float(s["wallet"].get("store_credit_usd", 0)), total), 2)
    bonus, bonus_offers = P.bonus_points(shopper_offers(s), subtotal)
    applied: dict[str, float] = {}
    for l in lines:
        for a in l.get("adjustments", []):
            if a["rule"] in ("overstock", "time", "offer", "bundle") and "pct" in a:
                applied[a["label"]] = round(applied.get(a["label"], 0) + l["list_price"] * a["pct"] / 100.0 * l["qty"], 2)
            elif a["rule"] == "competitor":
                applied[a["label"]] = applied.get(a["label"], 0)
    return {"subtotal_usd": subtotal, "delivery_fee_usd": fee, "tax_usd": tax, "total_usd": total,
            "savings_usd": round(sum(l["savings"] for l in lines), 2), "store_credit_applied_usd": credit,
            "amount_due_usd": round(total - credit, 2),
            "points_earned": int(subtotal) * tier["points_per_usd"] + bonus, "bonus_points": bonus,
            "promotions": [{"label": k, "saved_usd": v} for k, v in applied.items()],
            "offers_applied": sorted({a["id"] for l in lines for a in l.get("adjustments", []) if a["rule"] == "offer"} | set(bonus_offers)),
            "bundles_applied": sorted({a["id"] for l in lines for a in l.get("adjustments", []) if a["rule"] == "bundle"})}


def wallet_view(s: dict) -> dict:
    w = s["wallet"]
    return {"store_credit_usd": round(float(w.get("store_credit_usd", 0)), 2), "points": int(w.get("points", 0)),
            "payment_method": w.get("payment_method"), "tier": s["membership"]["tier"],
            "tier_name": s["membership"]["tier_name"], "discount_pct": s["membership"]["discount_pct"]}


def limit_check(s: dict, lines: list[dict]) -> list[dict]:
    """High-demand items carry a per-household limit over a rolling window."""
    out = []
    for l in lines:
        limit = C.PRODUCT_BY_SKU[l["sku"]].get("limit_per_household")
        if not limit:
            continue
        bought = sum(x["qty"] for o in s.get("order_history", []) for x in o.get("lines", [])
                     if x["sku"] == l["sku"] and (days_since(o.get("date")) or 0) <= C.PURCHASE_LIMIT_WINDOW_DAYS)
        if bought + l["qty"] > limit:
            out.append({"sku": l["sku"], "name": l["name"], "limit": limit, "already_bought": bought, "requested": l["qty"],
                        "window_days": C.PURCHASE_LIMIT_WINDOW_DAYS})
    return out


def _eta(option: str) -> str:
    days = {"Same-day delivery": 0, "Store pickup": 0, "2-day shipping": 2}.get(option, 3)
    return (today() + dt.timedelta(days=days)).isoformat()


def _next_order_id(s: dict) -> str:
    return f"SS-{today():%y%m%d}-{_h(s['shopper_id'], now_iso(), uuid.uuid4().hex) % 90000 + 10000}"


# --------------------------------------------------------------------------
# read-only tools
# --------------------------------------------------------------------------
async def get_shopper_context(shopper_id: str) -> dict:
    s = await load_shopper(shopper_id)
    last = s["order_history"][0] if s.get("order_history") else None
    return {
        "shopper_id": shopper_id, "first_name": s["first_name"], "household": s.get("household"),
        "membership": s["membership"], "wallet": wallet_view(s), "delivery": s["delivery"], "home_store": s.get("home_store"),
        "preferences": s.get("preferences", {}), "last_order": last,
        "days_since_last_order": days_since(last["date"]) if last else None,
        "spend": s.get("spend", {}), "subscriptions": s.get("subscriptions", []), "returns": s.get("returns", [])[:3],
        "segment": s["ml_scores"].get("segment"), "next_best_offer": s["ml_scores"].get("next_best_offer"),
    }


async def get_recommendations(shopper_id: str) -> dict:
    """The model's smart cart for this shopper (what is about to run out),
    member priced and checked against the fulfilling center's on-hand units,
    plus the 'for you' picks and any price-match candidates."""
    from . import personalization
    s = await load_shopper(shopper_id)
    recs = await personalization.compute_recommendations(s)
    lines = [l for l in recs["lines"] if l["selected"]]
    option = _option(s, "")
    tot = {}
    if lines:
        priced, _, _ = await _price(s, [{"sku": l["sku"], "qty": l["qty"]} for l in lines], option)
        tot = _totals(s, priced, option)
    return {"shopper_id": shopper_id, "tier": s["membership"]["tier"], "tier_name": s["membership"]["tier_name"],
            "fulfillment_center": s["delivery"]["fulfillment_center"], "delivery_option": option,
            "lines": lines, "totals": tot, "wallet": wallet_view(s), "for_you": recs["for_you"],
            "price_matches": recs["price_matches"], "offers": recs["offers"],
            "deals": (await get_deals(shopper_id))["deals"],
            "model_version": s["ml_scores"].get("model_version")}


async def search_catalog(shopper_id: str, query: str = "", category: str = "") -> dict:
    s = await load_shopper(shopper_id)
    inv, _ = await get_inventory(s["delivery"]["fulfillment_center"])
    store_id = (s.get("home_store") or {}).get("store_id")
    q = (query or "").lower().split()
    pricing = await P.get(s["delivery"]["fulfillment_center"])
    hour = P.hour_now(await P.get_signals())
    offers = shopper_offers(s)
    out = []
    for p in C.PRODUCTS:
        if category and p["category"] != category:
            continue
        hay = f"{p['name']} {p['sku']} {' '.join(p['tags'])} {C.CATEGORIES[p['category']]['name']}".lower()
        score = sum(1 for w in q if w in hay)
        if q and not score:
            continue
        rep = s.get("replenishment", {}).get(p["sku"])
        line = P.quote(s["membership"]["tier"], {p["sku"]: 1}, pricing, hour, "", offers)[0]
        out.append({**line, "deals": [a["label"] for a in line["adjustments"] if a["rule"] not in ("tier", "multi_buy")],
                    "on_hand_at_fc": inv["on_hand"].get(p["sku"], 0),
                    "aisle": C.aisle_for(store_id, p["sku"]) if store_id in C.STORES else None,
                    "buys_regularly": bool(rep), "avg_monthly_qty": (rep or {}).get("avg_monthly_qty"),
                    "limit_per_household": p.get("limit_per_household"), "score": score + (0.5 if rep else 0)})
    # On a tie the everyday item comes before its step-up (the value box, the pro model).
    step_ups = {up for up, _ in C.UPGRADES.values()}
    out.sort(key=lambda r: (-r["score"], r["sku"] in step_ups, r["name"]))
    return {"shopper_id": shopper_id, "items": out[:15]}


async def get_deals(shopper_id: str) -> dict:
    """(Not a tool of its own: `get_recommendations` returns it.) What is moving prices for this shopper right now: their personal
    offers, the dynamic price rules at their fulfillment center (overstock
    markdowns, competitor matches, the promotion for this hour) and bundles."""
    s = await load_shopper(shopper_id)
    pricing = await P.get(s["delivery"]["fulfillment_center"])
    hour = P.hour_now(await P.get_signals())
    offers = shopper_offers(s)
    tier = s["membership"]["tier"]
    deals = P.active_deals(pricing, hour)
    for d in deals:
        if d.get("sku"):
            d["your_price"] = P.unit_price(tier, d["sku"], pricing, hour, offers)
            d["list_price"] = C.PRODUCT_BY_SKU[d["sku"]]["list_price"]
            # A competitor match only matters to a shopper whose own price was above it.
            d["applies"] = d["kind"] == "overstock" or d["your_price"] == d.get("price_cap")
        elif d["kind"] == "bundle":
            priced = P.quote(tier, {x: 1 for x in d["skus"]}, pricing, hour, "", offers)
            d["bundle_price"] = round(sum(l["line_total"] for l in priced), 2)
            d["saves_usd"] = round(sum(C.PRODUCT_BY_SKU[x]["list_price"] * d["pct_off"] / 100.0 for x in d["skus"]), 2)
    return {"shopper_id": shopper_id, "fulfillment_center": pricing["fc"], "hour": hour, "pricing_version": pricing["version"],
            "offers": offers, "deals": deals, "tier_name": s["membership"]["tier_name"]}


async def quote_cart(shopper_id: str, lines: list, delivery_option: str = "") -> dict:
    s = await load_shopper(shopper_id)
    option = _option(s, delivery_option)
    ls, pricing, hour = await _price(s, lines, option)
    inv, _ = await get_inventory(s["delivery"]["fulfillment_center"])
    short_lines = [{"sku": l["sku"], "name": l["name"], "requested": l["qty"], "on_hand": inv["on_hand"].get(l["sku"], 0)}
                   for l in ls if inv["on_hand"].get(l["sku"], 0) < l["qty"]]
    return {"shopper_id": shopper_id, "lines": ls, "totals": _totals(s, ls, option), "delivery_option": option,
            "wallet": wallet_view(s), "short": short_lines, "purchase_limits": limit_check(s, ls),
            "fulfillment_center": s["delivery"]["fulfillment_center"], "pricing_version": pricing["version"], "priced_hour": hour}


# --------------------------------------------------------------------------
# mutating tools
# --------------------------------------------------------------------------
async def place_order(shopper_id: str, lines: list, delivery_option: str = "", notes: str = "", pricing_version: str = "",
                      priced_hour: int | None = None, sources: dict | None = None, _match: dict | None = None) -> dict:
    s = await load_shopper(shopper_id)
    option = _option(s, delivery_option)
    ls, pricing, hour = await _price(s, lines, option, _match, pricing_version, priced_hour)
    for l in ls:
        l["source"] = (sources or {}).get(l["sku"], "browse")
    over = limit_check(s, ls)
    if over:
        o = over[0]
        return {"ok": False, "status": "failed", "reason_code": "purchase_limit", "purchase_limits": over,
                "summary": f"{short(o['name'])} is limited to {o['limit']} per household every {o['window_days']} days, and "
                           f"your household bought {o['already_bought']} in that window. Remove it to place the rest of the order."}
    tot = _totals(s, ls, option)

    fc = s["delivery"]["fulfillment_center"]
    got = await allocate_inventory(fc, {l["sku"]: l["qty"] for l in ls})
    backordered = []
    for l in ls:
        l["shipped_qty"] = got[l["sku"]]
        l["backordered_qty"] = l["qty"] - got[l["sku"]]
        if l["backordered_qty"]:
            backordered.append({"sku": l["sku"], "name": l["name"], "qty": l["backordered_qty"],
                                "eta": (today() + dt.timedelta(days=5 + _h(fc, l["sku"]) % 6)).isoformat()})
    order_id = _next_order_id(s)
    status = "partially_shipped" if backordered else "confirmed"
    store_name = (s.get("home_store") or {}).get("name")
    order = {"order_id": order_id, "date": today().isoformat(), "placed_at": now_iso(), "status": status,
             "channel": "app", "delivery_option": option, "eta": _eta(option), "fulfillment_center": fc,
             "pickup_store": store_name if option == C.PICKUP_OPTION else None, "notes": (notes or "").strip()[:300],
             "lines": ls, "line_count": len(ls), **tot, "backordered": backordered, "price_match": bool(_match),
             "pricing_version": pricing["version"], "priced_hour": hour,
             "price_locked": bool(pricing_version) and pricing["version"] == pricing_version}
    # live state on the shopper: wallet, history, replenishment
    w = s["wallet"]
    w["store_credit_usd"] = round(float(w.get("store_credit_usd", 0)) - tot["store_credit_applied_usd"], 2)
    w["points"] = int(w.get("points", 0)) + tot["points_earned"]
    s["order_history"] = ([order] + s.get("order_history", []))[:24]
    for l in ls:
        if C.PRODUCT_BY_SKU[l["sku"]]["consumable"]:
            r = s.setdefault("replenishment", {}).setdefault(l["sku"], {"avg_monthly_qty": l["qty"]})
            r["last_ordered"] = today().isoformat()
            r["last_qty"] = l["qty"]
    sp = s.setdefault("spend", {})
    sp["ytd_usd"] = round(sp.get("ytd_usd", 0) + tot["total_usd"], 2)
    sp["orders_last_12mo"] = sp.get("orders_last_12mo", 0) + 1
    once = {o["offer_id"] for o in shopper_offers(s) if o.get("once")}
    s["redeemed_offers"] = sorted(set(s.get("redeemed_offers", [])) | (once & set(tot["offers_applied"])))
    await save_shopper(s)
    metrics.merchandising(order)
    # Stock just moved: the rules for this fulfillment center may have changed (a markdown ends, a bundle pauses).
    await P.refresh(fc, f"order {order_id}")
    await after_change(shopper_id)
    bo = (f" {len(backordered)} item{'s' if len(backordered) > 1 else ''} backordered: "
          + "; ".join(f"{short(b['name'])} x{b['qty']} (ETA {b['eta']})" for b in backordered) + ".") if backordered else ""
    where = (f"Pickup at {store_name}, ready today" if option == C.PICKUP_OPTION
             else f"{option}, arriving {order['eta']} from {C.FULFILLMENT_CENTERS[fc]['name']}")
    credit = f" {money(tot['store_credit_applied_usd'])} store credit applied." if tot["store_credit_applied_usd"] else ""
    promos = [p["label"] for p in tot["promotions"]]
    if promos:
        credit += " Applied: " + ", ".join(promos) + "."
    if tot["bonus_points"]:
        credit += f" Includes {tot['bonus_points']} bonus points."
    return {"ok": True, "status": status, "charge_usd": tot["amount_due_usd"], "item": order,
            "summary": f"Order {order_id} placed: {len(ls)} item{'s' if len(ls) > 1 else ''}, {money(tot['total_usd'])} "
                       f"({money(tot['savings_usd'])} saved, {tot['points_earned']} points earned).{credit} {where}.{bo}"}


async def request_price_match(shopper_id: str, lines: list, sku: str, competitor: str, competitor_price_usd: float,
                              delivery_option: str = "", notes: str = "", pricing_version: str = "",
                              priced_hour: int | None = None, sources: dict | None = None) -> dict:
    """Place an order with one item matched to a competitor's lower price.
    Held for a Customer Care decision in AOM's approval tier before it can
    run; once approved the item is re-priced and the order is placed."""
    s = await load_shopper(shopper_id)
    lock = {"pricing_version": pricing_version, "priced_hour": priced_hour, "sources": sources}
    ls, _, _ = await _price(s, lines, _option(s, delivery_option), None, pricing_version, priced_hour)
    line = next((l for l in ls if l["sku"] == sku), None)
    if not line:
        raise ToolError(f"{sku} isn't in this order, so there is nothing to match")
    if competitor not in C.COMPETITORS:
        raise ToolError(f"We price match {', '.join(C.COMPETITORS)}")
    price = round(float(competitor_price_usd), 2)
    if price >= line["unit_price"]:
        # Nothing to match: our member price is already as low. Just place it.
        return await place_order(shopper_id, lines, delivery_option, notes, **lock)
    floor = round(line["list_price"] * C.PRICE_MATCH_FLOOR_PCT / 100.0, 2)
    if price < floor:
        raise ToolError(f"A matched price can't go below {money(floor)} for {short(line['name'])} "
                        f"({C.PRICE_MATCH_FLOOR_PCT}% of list)")
    saving = round((line["unit_price"] - price) * line["qty"], 2)
    if saving > C.PRICE_MATCH_MAX_SAVING_USD:
        raise ToolError(f"Price matches are capped at {money(C.PRICE_MATCH_MAX_SAVING_USD)} per order")
    res = await place_order(shopper_id, lines, delivery_option,
                            notes or f"Price match: {competitor} at {money(price)}", **lock, _match={sku: price})
    if res.get("ok"):
        res["summary"] = (f"Price match applied: {short(line['name'])} at {competitor}'s {money(price)}, saving {money(saving)}. "
                          + res["summary"])
        res["item"]["price_match"] = {"sku": sku, "competitor": competitor, "matched_price_usd": price, "saved_usd": saving}
    return res


async def set_subscription(shopper_id: str, sku: str, qty: int, cadence_weeks: int = 4) -> dict:
    s = await load_shopper(shopper_id)
    ln = (await _price(s, [{"sku": sku, "qty": qty}]))[0][0]
    # A subscription is priced at the member tier; promotions of the day don't carry into future shipments.
    ln["unit_price"] = C.member_price(sku, s["membership"]["tier"], ln["qty"])
    if not C.PRODUCT_BY_SKU[sku]["consumable"]:
        raise ToolError("Subscribe & Save is for everyday essentials you reorder, not one-time purchases")
    cadence = int(cadence_weeks)
    if cadence not in (1, 2, 4, 6, 8, 12):
        raise ToolError("Cadence must be 1, 2, 4, 6, 8 or 12 weeks")
    sub = {"sku": sku, "name": ln["name"], "qty": ln["qty"], "unit_price": ln["unit_price"], "cadence_weeks": cadence,
           "next_date": (today() + dt.timedelta(weeks=cadence)).isoformat(), "created_at": now_iso()}
    s["subscriptions"] = [x for x in s.get("subscriptions", []) if x["sku"] != sku] + [sub]
    await save_shopper(s)
    await after_change(shopper_id)
    return {"ok": True, "status": "confirmed", "charge_usd": 0, "item": {**sub, "kind": "subscription"},
            "summary": f"Subscribe & Save set: {ln['qty']} x {short(ln['name'])} every {cadence} week"
                       f"{'s' if cadence > 1 else ''} at {money(ln['unit_price'])}, next on {sub['next_date']}."}


async def create_return(shopper_id: str, order_id: str, sku: str, qty: int, reason: str) -> dict:
    s = await load_shopper(shopper_id)
    order = next((o for o in s.get("order_history", []) if o.get("order_id") == order_id), None)
    if not order:
        raise ToolError(f"Order {order_id} isn't in your recent orders")
    if (days_since(order["date"]) or 0) > C.RETURN_WINDOW_DAYS:
        raise ToolError(f"Returns are accepted within {C.RETURN_WINDOW_DAYS} days of delivery")
    line = next((l for l in order.get("lines", []) if l["sku"] == sku), None)
    if not line:
        raise ToolError(f"{sku} wasn't on order {order_id}")
    qty = int(qty)
    already = sum(r["qty"] for r in s.get("returns", []) if r["order_id"] == order_id and r["sku"] == sku)
    if qty < 1 or qty + already > line["qty"]:
        raise ToolError(f"Return quantity must be between 1 and {line['qty'] - already}")
    if not reason or len(reason.strip()) < 3:
        raise ToolError("A return needs a reason")
    refund = round(line["unit_price"] * qty, 2)
    store_name = (s.get("home_store") or {}).get("name") or "any ShopSmart store"
    rma = {"return_id": f"RTN-{_h(order_id, sku, now_iso()) % 900000 + 100000}", "order_id": order_id, "sku": sku,
           "name": line["name"], "qty": qty, "refund_usd": refund, "reason": reason.strip()[:200], "date": today().isoformat(),
           "drop_off": store_name, "kind": "return"}
    s["wallet"]["store_credit_usd"] = round(float(s["wallet"].get("store_credit_usd", 0)) + refund, 2)
    s["returns"] = ([rma] + s.get("returns", []))[:12]
    await save_shopper(s)
    await after_change(shopper_id)
    return {"ok": True, "status": "confirmed", "charge_usd": -refund, "item": rma,
            "summary": f"{rma['return_id']} created: {qty} x {short(line['name'])} from {order_id}. {money(refund)} is in your "
                       f"store credit now. Drop it at the returns desk at {store_name} or use the prepaid label within 14 days."}


async def update_preferences(shopper_id: str, favorite_categories: list | None = None, avoid_categories: list | None = None,
                             default_delivery_option: str = "", allow_substitutions: bool | None = None) -> dict:
    """What the shopper wants more or less of. Changes how the
    recommendations are ranked on the next sync."""
    s = await load_shopper(shopper_id)
    prefs = s.setdefault("preferences", {})
    for name, value in (("favorite_categories", favorite_categories), ("avoid_categories", avoid_categories)):
        if value is None:
            continue
        bad = [c for c in value if c not in C.CATEGORIES]
        if bad:
            raise ToolError(f"Unknown categor{'ies' if len(bad) > 1 else 'y'}: {', '.join(bad)}")
        prefs[name] = sorted(set(value))
    clash = set(prefs.get("favorite_categories", [])) & set(prefs.get("avoid_categories", []))
    if clash:
        raise ToolError(f"{C.CATEGORIES[sorted(clash)[0]]['name']} can't be both a favorite and hidden")
    if default_delivery_option:
        s["delivery"]["default_option"] = _option(s, default_delivery_option)
    if allow_substitutions is not None:
        prefs["allow_substitutions"] = bool(allow_substitutions)
    prefs["updated_at"] = now_iso()
    await save_shopper(s)
    await after_change(shopper_id)
    fav = ", ".join(C.CATEGORIES[c]["name"] for c in prefs.get("favorite_categories", [])) or "none"
    hide = ", ".join(C.CATEGORIES[c]["name"] for c in prefs.get("avoid_categories", [])) or "none"
    return {"ok": True, "status": "confirmed", "charge_usd": 0,
            "item": {"kind": "preferences", **{k: v for k, v in prefs.items()}, "default_delivery_option": s["delivery"].get("default_option")},
            "summary": f"Preferences saved. More of: {fav}. Hidden: {hide}. Default delivery: "
                       f"{s['delivery'].get('default_option')}. Your recommendations have been re-ranked."}


async def issue_goodwill_credit(shopper_id: str, amount_usd: float, reason: str) -> dict:
    """A goodwill store credit (damaged delivery, missed window). Held for a
    human decision in AOM's approval tier before it is applied."""
    s = await load_shopper(shopper_id)
    amt = round(float(amount_usd), 2)
    if amt <= 0 or amt > C.GOODWILL_MAX_USD:
        raise ToolError(f"Goodwill credits are between $0.01 and {money(C.GOODWILL_MAX_USD)}")
    if not reason or len(reason.strip()) < 5:
        raise ToolError("A goodwill credit needs a reason for the audit trail")
    s["wallet"]["store_credit_usd"] = round(float(s["wallet"].get("store_credit_usd", 0)) + amt, 2)
    item = {"kind": "goodwill_credit", "credit_id": f"GC-{_h(shopper_id, now_iso()) % 900000 + 100000}", "amount_usd": amt,
            "reason": reason.strip()[:200], "date": today().isoformat()}
    s["credits"] = ([item] + s.get("credits", []))[:12]
    await save_shopper(s)
    await after_change(shopper_id)
    return {"ok": True, "status": "confirmed", "charge_usd": -amt, "item": item,
            "summary": f"Goodwill credit of {money(amt)} added to your store credit - it comes off your next order "
                       f"automatically. Reason recorded: {reason.strip()[:120]}"}


# --------------------------------------------------------------------------
# store tools (pos.py, vision.py): the lane, the phone line and self-checkout
# --------------------------------------------------------------------------
async def identify_guest(shopper_id: str = "", member_number: str = "", store_id: str = "") -> dict:
    from . import pos
    return await pos.identify_guest(shopper_id=shopper_id, member_number=member_number, store_id=store_id)


async def place_pos_order(**kw) -> dict:
    from . import pos
    return await pos.place_pos_order(**kw)


async def resolve_checkout_exception(**kw) -> dict:
    from . import vision
    return await vision.resolve_checkout_exception(**kw)


TOOLS = {
    "get_shopper_context": get_shopper_context,
    "get_recommendations": get_recommendations,
    "search_catalog": search_catalog,
    "quote_cart": quote_cart,
    "place_order": place_order,
    "request_price_match": request_price_match,
    "set_subscription": set_subscription,
    "create_return": create_return,
    "update_preferences": update_preferences,
    "issue_goodwill_credit": issue_goodwill_credit,
    "identify_guest": identify_guest,
    "place_pos_order": place_pos_order,
    "resolve_checkout_exception": resolve_checkout_exception,
}
READ_ONLY_TOOLS = {"get_shopper_context", "get_recommendations", "search_catalog", "quote_cart", "identify_guest"}
APPROVAL_TOOLS = ["request_price_match", "issue_goodwill_credit", "resolve_checkout_exception"]
# Only a store device (a ticket or kiosk session written by this backend) may run these; a guest's phone may not.
STORE_ONLY_TOOLS = {"place_pos_order", "resolve_checkout_exception"}
