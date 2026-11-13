"""Dynamic pricing and bundling - the merchandising rules engine.

One `pricing::<fc>` document per fulfillment center, rebuilt whenever its
inputs change:

* **inventory** - an overstocked item is marked down; an item that is nearly
  sold out, or that the demand forecast expects to outrun supply, is taken
  out of every promotion and out of any bundle that contains it
* **hours** - promotions tied to when the business is quiet or busy (a
  breakfast promotion in the quiet morning delivery hours, a pickup discount
  in the evening delivery peak)
* **competitor data** - a competitor price a little under ours is matched
  automatically; a bigger gap stays a price-match request for Customer Care
* **bundles** - curated bundles from market-basket analysis, plus one the
  engine builds itself from the most overstocked item and what sells with it

The document carries the rules, not the prices. `quote()` turns rules + a
cart into prices, and `mobile/src/pricing.ts` is the same arithmetic on the
phone, so a cart is priced identically with or without signal. The document
is written to the `catalog` collection on the `fc:<fc>` channel; Sync Gateway
puts it on every phone served by that fulfillment center.

**Price lock.** Every distinct rule set is also kept for 48 hours as
`pricinghist::<fc>::<version>`. An order sent from a phone names the version
and the hour it was priced at, and is priced with exactly those rules - so a
cart built in a tunnel at 9:40 keeps the breakfast promotion even when it
syncs at 10:05.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import logging
from zoneinfo import ZoneInfo

from . import catalog as C
from . import config
from .docs import inventory_id, now_iso
from .store import store

log = logging.getLogger("pos.pricing")
HISTORY_TTL_SECONDS = 48 * 3600
# What the engine changed and why, newest first - shown on the merchandising console.
CHANGES: collections.deque = collections.deque(maxlen=60)
STATS = {"repricings": 0, "last_refresh_at": None}


def pricing_id(fc: str) -> str:
    return f"pricing::{fc}"


def fc_channel(fc: str) -> str:
    return f"fc:{fc}"


def _short(name: str) -> str:
    return name.split(",")[0]


# --------------------------------------------------------------------------
# signals: what the engine reacts to besides inventory
# --------------------------------------------------------------------------
def default_signals() -> dict:
    """The same content `databricks/generate_mock_dump.py` writes to
    `_pricing_signals.json`; used when an export carries none."""
    return {
        "source": "defaults",
        "market_prices": {sku: {"competitor": c, "price_usd": p} for sku, (c, p) in C.MARKET_PRICES.items()},
        "high_demand": list(C.HIGH_DEMAND),
        "time_windows": [dict(w) for w in C.TIME_WINDOWS],
        "bundles": [dict(b) for b in C.BUNDLES],
        "pairs_with": {k: [{"sku": s, "reason": r} for s, r in v] for k, v in C.PAIRS_WITH.items()},
        "upgrades": {k: {"sku": s, "reason": r} for k, (s, r) in C.UPGRADES.items()},
        "clock": {"tz": config.PRICING_TZ, "demo_hour": config.DEMO_HOUR},
    }


async def get_signals() -> dict:
    sig = await store.get("lookups", "pricing_signals")
    if not sig:
        sig = default_signals()
        await store.upsert("lookups", "pricing_signals", sig)
    return sig


async def set_signals(sig: dict):
    sig["updated_at"] = now_iso()
    await store.upsert("lookups", "pricing_signals", sig)


def hour_now(signals: dict) -> int:
    """The hour the time-window rules see: the demo clock when one is set,
    otherwise the wall clock in the pricing time zone."""
    clock = signals.get("clock") or {}
    if clock.get("demo_hour") is not None:
        return int(clock["demo_hour"]) % 24
    return dt.datetime.now(ZoneInfo(clock.get("tz") or config.PRICING_TZ)).hour


# --------------------------------------------------------------------------
# rules for one fulfillment center
# --------------------------------------------------------------------------
def build(fc: str, on_hand: dict, signals: dict) -> dict:
    """Pure: inventory + signals -> the rule document."""
    sku_rules: dict[str, dict] = {}
    ratios = {}
    for p in C.PRODUCTS:
        sku = p["sku"]
        have = int(on_hand.get(sku, 0))
        level = C.stock_level(sku)
        ratio = have / level
        ratios[sku] = ratio
        rules = {}
        if sku in signals.get("high_demand", []):
            rules["no_promo"] = {"label": "High demand", "reason": "The forecast expects demand to outrun supply, so it is never promoted"}
        elif ratio <= C.LOW_STOCK_RATIO:
            rules["no_promo"] = {"label": "Selling fast" if have else "Sold out",
                                 "reason": (f"Only {have} on hand" if have else "None on hand")
                                           + f" against a normal level of {level}: promotions are paused"}
        else:
            for min_ratio, pct in C.OVERSTOCK_TIERS:
                if ratio >= min_ratio:
                    rules["overstock"] = {"pct_off": pct, "label": "Overstock markdown",
                                          "reason": f"{have} on hand, {ratio:.1f}x the normal level of {level}"}
                    break
        mp = (signals.get("market_prices") or {}).get(sku)
        if mp and "no_promo" not in rules and mp["price_usd"] < p["list_price"]:
            if mp["price_usd"] >= p["list_price"] * C.AUTO_MATCH_MIN_PCT_OF_LIST / 100.0:
                rules["competitor"] = {"price_cap": round(float(mp["price_usd"]), 2), "competitor": mp["competitor"],
                                       "label": f"Matched to {mp['competitor']}",
                                       "reason": f"{mp['competitor']} is at ${mp['price_usd']:,.2f}, within "
                                                 f"{100 - C.AUTO_MATCH_MIN_PCT_OF_LIST}% of our list price: matched automatically"}
        if rules:
            sku_rules[sku] = rules

    blocked = {sku for sku, r in sku_rules.items() if "no_promo" in r}
    bundles = []
    for b in signals.get("bundles", []):
        skus = [s for s in b["skus"] if s in C.PRODUCT_BY_SKU]
        stop = [s for s in skus if s in blocked]
        bundles.append({"id": b["id"], "name": b["name"], "skus": skus, "pct_off": b["pct_off"], "reason": b.get("reason", ""),
                        "kind": "curated", "active": not stop,
                        "paused_reason": f"{_short(C.PRODUCT_BY_SKU[stop[0]]['name'])} is nearly sold out" if stop else None})
    # One bundle the engine builds itself: the most overstocked item, with whatever sells with it.
    over = sorted(((r, sku) for sku, r in ratios.items() if "overstock" in sku_rules.get(sku, {})), reverse=True)
    pairs = signals.get("pairs_with") or {}
    for _, sku in over:
        # Pair it with an item whose shoppers already get pointed at it, or failing that with what it sells with.
        other = next((o for o, lst in pairs.items() if o in C.PRODUCT_BY_SKU and o not in blocked
                      and any(x["sku"] == sku for x in lst)), None)
        other = other or next((x["sku"] for x in pairs.get(sku, []) if x["sku"] not in blocked), None)
        if other:
            bundles.append({"id": f"clear-out-{sku}", "name": f"{_short(C.PRODUCT_BY_SKU[sku]['name'])} clear-out",
                            "skus": [other, sku], "pct_off": C.DYNAMIC_BUNDLE_PCT, "kind": "dynamic", "active": True, "paused_reason": None,
                            "reason": f"Built by the pricing engine: {_short(C.PRODUCT_BY_SKU[sku]['name'])} is overstocked at this "
                                      "fulfillment center"})
            break

    rules_only = {"sku_rules": sku_rules, "time_rules": signals.get("time_windows", []), "bundles": bundles,
                  "max_discount_pct": C.MAX_DISCOUNT_PCT, "clock": signals.get("clock") or {}}
    version = hashlib.sha1(json.dumps(rules_only, sort_keys=True).encode()).hexdigest()[:12]
    return {"type": "pricing", "fc": fc, "fc_name": C.FULFILLMENT_CENTERS[fc]["name"], "version": version, **rules_only,
            "on_hand": {p["sku"]: int(on_hand.get(p["sku"], 0)) for p in C.PRODUCTS},
            "channels": [fc_channel(fc)]}


def _diff(fc: str, old: dict | None, new: dict, why: str):
    def label(sku):
        return _short(C.PRODUCT_BY_SKU[sku]["name"])
    before, after = (old or {}).get("sku_rules", {}), new["sku_rules"]
    for sku in sorted(set(before) | set(after)):
        b, a = before.get(sku, {}), after.get(sku, {})
        for kind in ("overstock", "no_promo", "competitor"):
            if kind in a and a[kind] != b.get(kind):
                what = (f"{a[kind]['pct_off']}% off" if kind == "overstock" else
                        f"capped at ${a[kind]['price_cap']:,.2f}" if kind == "competitor" else "promotions paused")
                CHANGES.appendleft({"at": now_iso(), "fc": fc, "sku": sku, "kind": kind, "why": why,
                                    "text": f"{label(sku)}: {a[kind]['label']} - {what}. {a[kind]['reason']}"})
            elif kind in b and kind not in a:
                CHANGES.appendleft({"at": now_iso(), "fc": fc, "sku": sku, "kind": f"{kind}_ended", "why": why,
                                    "text": f"{label(sku)}: {b[kind]['label'].lower()} ended"})
    was = {b["id"]: b for b in (old or {}).get("bundles", [])}
    for b in new["bundles"]:
        prev = was.get(b["id"])
        if prev is None and old is not None:
            CHANGES.appendleft({"at": now_iso(), "fc": fc, "sku": None, "kind": "bundle", "why": why,
                                "text": f"New bundle: {b['name']} ({b['pct_off']}% off). {b['reason']}"})
        elif prev is not None and prev.get("active") != b["active"]:
            CHANGES.appendleft({"at": now_iso(), "fc": fc, "sku": None, "kind": "bundle", "why": why,
                                "text": f"Bundle {b['name']} {'is back' if b['active'] else 'paused: ' + (b['paused_reason'] or '')}"})
    if old is not None and (old.get("clock") or {}) != (new.get("clock") or {}):
        CHANGES.appendleft({"at": now_iso(), "fc": fc, "sku": None, "kind": "clock", "why": why,
                            "text": f"Pricing clock set to {new['clock'].get('demo_hour')}:00" if new["clock"].get("demo_hour") is not None
                            else "Pricing clock back on wall time"})


async def refresh(fc: str, why: str = "timer") -> dict:
    """Rebuild one fulfillment center's rules; write only when something changed."""
    from .actions import get_inventory  # late import: actions imports this module
    inv, _ = await get_inventory(fc)
    signals = await get_signals()
    doc = build(fc, inv["on_hand"], signals)
    old = await store.get("catalog", pricing_id(fc))
    if old and old.get("version") == doc["version"] and old.get("on_hand") == doc["on_hand"]:
        return old
    doc["generated_at"] = now_iso()
    doc["updated_at"] = doc["generated_at"]
    if not old or old.get("version") != doc["version"]:
        _diff(fc, old, doc, why)
        STATS["repricings"] += 1 if old else 0
        await store.upsert("lookups", f"pricinghist::{fc}::{doc['version']}",
                           {k: v for k, v in doc.items() if k not in ("channels", "on_hand")}, ttl=HISTORY_TTL_SECONDS)
    await store.upsert("catalog", pricing_id(fc), doc)
    STATS["last_refresh_at"] = doc["generated_at"]
    return doc


async def refresh_all(why: str = "timer"):
    for fc in C.FULFILLMENT_CENTERS:
        try:
            await refresh(fc, why)
        except Exception as exc:  # one busy inventory doc must not stop the others
            log.warning("pricing refresh for %s failed: %s", fc, exc)


async def get(fc: str, version: str | None = None) -> dict:
    """The current rules, or the exact version a phone priced a cart with."""
    if version:
        cur = await store.get("catalog", pricing_id(fc))
        if cur and cur.get("version") == version:
            return cur
        hist = await store.get("lookups", f"pricinghist::{fc}::{version}")
        if hist:
            return hist
    return await store.get("catalog", pricing_id(fc)) or await refresh(fc, "first use")


# --------------------------------------------------------------------------
# rules + cart -> prices (mirrored by mobile/src/pricing.ts)
# --------------------------------------------------------------------------
def window_active(rule: dict, hour: int) -> bool:
    s, e = int(rule["start_hour"]), int(rule["end_hour"])
    return s <= hour < e if s <= e else (hour >= s or hour < e)


def _window_matches(rule: dict, product: dict, option: str) -> bool:
    if rule.get("requires_option") and rule["requires_option"] != option:
        return False
    if rule.get("categories") and product["category"] not in rule["categories"]:
        return False
    if rule.get("tags") and not set(rule["tags"]) & set(product["tags"]):
        return False
    return True


def offer_matches(offer: dict, product: dict) -> bool:
    if offer.get("kind") == "category_pct":
        return product["category"] == offer.get("category")
    if offer.get("kind") == "sku_pct":
        return product["sku"] == offer.get("sku")
    return False


def quote(tier_code: str, cart: dict[str, int], pricing: dict, hour: int, option: str = "", offers: list | None = None,
          match: dict | None = None) -> list[dict]:
    """Price a cart. Every line lists the adjustments behind its price, in
    the order they are applied: member tier, multi-buy, overstock, the hour,
    a personal offer, a bundle (all percentages off list, added together up
    to the cap), then a competitor price cap, then an approved price match."""
    tier = C.TIER_BY_CODE[tier_code]
    sku_rules = pricing.get("sku_rules", {})
    cap_pct = pricing.get("max_discount_pct", C.MAX_DISCOUNT_PCT)
    bundles = [b for b in pricing.get("bundles", []) if b.get("active") and all(cart.get(s, 0) >= 1 for s in b["skus"])]
    lines = []
    for sku, qty in cart.items():
        p = C.PRODUCT_BY_SKU[sku]
        rules = sku_rules.get(sku, {})
        adj = []
        if tier["discount_pct"]:
            adj.append({"rule": "tier", "label": f"{tier['name']} member", "pct": tier["discount_pct"]})
        for min_qty, extra in C.MULTI_BUY:
            if qty >= min_qty:
                adj.append({"rule": "multi_buy", "label": f"Multi-buy {min_qty}+", "pct": extra})
                break
        if "no_promo" not in rules:
            if "overstock" in rules:
                adj.append({"rule": "overstock", "label": rules["overstock"]["label"], "pct": rules["overstock"]["pct_off"]})
            windows = [w for w in pricing.get("time_rules", []) if window_active(w, hour) and _window_matches(w, p, option)]
            if windows:
                w = max(windows, key=lambda x: x["pct_off"])
                adj.append({"rule": "time", "id": w["id"], "label": w["label"], "pct": w["pct_off"]})
            mine = [o for o in (offers or []) if offer_matches(o, p)]
            if mine:
                o = max(mine, key=lambda x: x["pct_off"])
                adj.append({"rule": "offer", "id": o["offer_id"], "label": o["title"], "pct": o["pct_off"]})
            mine = [b for b in bundles if sku in b["skus"]]
            if mine:
                b = max(mine, key=lambda x: x["pct_off"])
                adj.append({"rule": "bundle", "id": b["id"], "label": f"{b['name']} bundle", "pct": b["pct_off"]})
        pct = min(cap_pct, sum(a["pct"] for a in adj))
        unit = round(p["list_price"] * (1 - pct / 100.0), 2)
        cap = (rules.get("competitor") or {}).get("price_cap") if "no_promo" not in rules else None
        if cap is not None and cap < unit:
            unit = cap
            adj.append({"rule": "competitor", "label": rules["competitor"]["label"], "price": cap})
        line = {"sku": sku, "name": p["name"], "category": p["category"], "uom": p["uom"], "qty": qty,
                "list_price": p["list_price"], "unit_price": unit, "line_total": round(unit * qty, 2),
                "savings": round((p["list_price"] - unit) * qty, 2), "adjustments": adj}
        matched = (match or {}).get(sku)
        if matched is not None and matched < unit:
            line.update(unit_price=matched, line_total=round(matched * qty, 2), member_unit_price=unit,
                        savings=round((p["list_price"] - matched) * qty, 2), price_matched=True)
            line["adjustments"] = adj + [{"rule": "price_match", "label": "Price match", "price": matched}]
        lines.append(line)
    return lines


def bonus_points(offers: list | None, subtotal: float) -> tuple[int, list[str]]:
    pts, used = 0, []
    for o in offers or []:
        if o.get("kind") == "bonus_points" and subtotal >= float(o.get("min_subtotal_usd", 0)):
            pts += int(o["points"])
            used.append(o["offer_id"])
    return pts, used


def unit_price(tier_code: str, sku: str, pricing: dict, hour: int, offers: list | None = None, qty: int = 1) -> float:
    return quote(tier_code, {sku: qty}, pricing, hour, "", offers)[0]["unit_price"]


def active_deals(pricing: dict, hour: int) -> list[dict]:
    """Everything that is moving a price right now, for a 'today's deals' list."""
    out = []
    for sku, rules in pricing.get("sku_rules", {}).items():
        p = C.PRODUCT_BY_SKU.get(sku)
        if not p:
            continue
        if "overstock" in rules:
            out.append({"kind": "overstock", "sku": sku, "name": p["name"], "label": rules["overstock"]["label"],
                        "pct_off": rules["overstock"]["pct_off"], "reason": rules["overstock"]["reason"]})
        if "competitor" in rules and "no_promo" not in rules:
            out.append({"kind": "competitor", "sku": sku, "name": p["name"], "label": rules["competitor"]["label"],
                        "price_cap": rules["competitor"]["price_cap"], "reason": rules["competitor"]["reason"]})
    for w in pricing.get("time_rules", []):
        if window_active(w, hour):
            out.append({"kind": "time", "id": w["id"], "label": w["label"], "pct_off": w["pct_off"], "reason": w["reason"],
                        "until_hour": w["end_hour"], "requires_option": w.get("requires_option"), "peak": bool(w.get("peak"))})
    for b in pricing.get("bundles", []):
        if b.get("active"):
            out.append({"kind": "bundle", "id": b["id"], "label": f"{b['name']} bundle", "pct_off": b["pct_off"], "skus": b["skus"],
                        "names": [_short(C.PRODUCT_BY_SKU[s]["name"]) for s in b["skus"]], "reason": b["reason"],
                        "dynamic": b.get("kind") == "dynamic"})
    return out
