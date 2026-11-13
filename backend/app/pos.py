"""The store side of ShopSmart: one priced cart, one order and one ticket
queue shared by the drive-thru lane, the phone line and self-checkout.

Everything a store device needs is a document the store already holds: the
catalog, the café menu, the pricing rules for its fulfillment center
(`pricing::<fc>`), the guest's profile and recommendations, and this store's
edge state (`posstate::<store>`: WAN link, kitchen counts, recent tickets).
So the lane keeps taking orders and the kiosks keep checking out when the
WAN is down. A finished order is a *ticket* in the `orders` collection:

    ticket::<store>::<uuid>   type=request, origin=pos, tool=place_pos_order

The kitchen display reads it at once (it is on the `store:<id>` channel).
With the WAN up the worker runs it through AOM like any shopper order; with
the WAN down it waits as `on_edge` and is released the moment the link
returns. For an identified guest the ticket is also on their `shopper:<id>`
channel, so it shows up in the Orders tab of their phone.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import uuid

from . import actions as A
from . import catalog as C
from . import menu as M
from . import metrics
from . import pricing as P
from .docs import STATE, money, now_iso, recs_id, shopper_channel, store_channel, today
from .store import store

CHANNELS = {"drive_thru": "Drive-thru", "phone": "Phone order", "self_checkout": "Self-checkout"}
MAX_TICKETS = 40


def state_id(store_id: str) -> str:
    return f"posstate::{store_id}"


def _store(store_id: str) -> dict:
    s = C.STORES.get(store_id)
    if not s:
        raise A.ToolError(f"No store '{store_id}'")
    return s


def default_kitchen() -> dict:
    return {m["sku"]: {"on_hand": m["kitchen_par"], "par": m["kitchen_par"]} for m in M.MENU if m.get("kitchen_par")}


async def get_state(store_id: str) -> dict:
    s = _store(store_id)
    doc = await store.get("lookups", state_id(store_id))
    if not doc:
        doc = {"type": "pos_state", "store_id": store_id, "store_name": s["name"], "wan": "up", "kitchen": default_kitchen(),
               "tickets": [], "created_at": now_iso()}
        await store.upsert("lookups", state_id(store_id), doc)
    return doc


async def save_state(doc: dict):
    doc["updated_at"] = now_iso()
    await store.upsert("lookups", state_id(doc["store_id"]), doc)


def kitchen_rules(state: dict) -> dict:
    """What the kitchen counts do to the menu: overbaked items are marked
    down (and become what the lane suggests), sold-out items are 86'd."""
    out = {}
    for sku, k in (state.get("kitchen") or {}).items():
        if k["on_hand"] <= 0:
            out[sku] = {"out": True}
        elif k["on_hand"] >= k["par"] * M.KITCHEN_OVERSTOCK_RATIO:
            out[sku] = {"overstock": M.KITCHEN_OVERSTOCK_PCT,
                        "label": f"Fresh batch · {M.KITCHEN_OVERSTOCK_PCT}% off ({k['on_hand']} made, {k['par']} is normal)"}
    return out


async def context(store_id: str, shopper: dict | None, pricing_version: str = "", priced_hour: int | None = None) -> dict:
    """What pricing needs for one cart at one store: the rules for the store's
    fulfillment center (optionally a locked version), the hour, the guest's
    tier and offers, and the kitchen."""
    s = _store(store_id)
    fc = s["fulfillment_center"]
    pricing = await P.get(fc, pricing_version or None)
    hour = int(priced_hour) % 24 if priced_hour is not None and str(priced_hour) != "" else P.hour_now(await P.get_signals())
    state = await get_state(store_id)
    return {"store_id": store_id, "store": s, "fc": fc, "pricing": pricing, "hour": hour, "state": state,
            "kitchen": kitchen_rules(state), "tier": shopper["membership"]["tier"] if shopper else "MEMBER",
            "offers": A.shopper_offers(shopper) if shopper else [], "shopper": shopper}


def split(lines) -> tuple[dict[str, int], list[dict]]:
    """Retail lines merged by SKU, menu lines merged by item + size + modifiers."""
    if not isinstance(lines, list) or not lines:
        raise A.ToolError("An order needs at least one item")
    retail: dict[str, int] = {}
    menu: dict[str, dict] = {}
    for ln in lines:
        if not isinstance(ln, dict) or not ln.get("sku"):
            raise A.ToolError("Each line needs a sku and qty")
        sku = ln["sku"]
        if M.is_menu(sku):
            n = M.normalize_line(ln)
            key = M.line_key(n["sku"], n["size"], n["mods"])
            if key in menu:
                menu[key]["qty"] += n["qty"]
            else:
                menu[key] = n
        elif sku in C.PRODUCT_BY_SKU:
            retail[sku] = retail.get(sku, 0) + int(ln.get("qty") or 1)
        else:
            raise A.ToolError(f"Unknown item '{sku}'")
    for sku, qty in retail.items():
        if qty < 1 or qty > 24:
            raise A.ToolError(f"Quantity for {A.short(C.PRODUCT_BY_SKU[sku]['name'])} must be between 1 and 24")
    for n in menu.values():
        if n["qty"] < 1 or n["qty"] > 24:
            raise A.ToolError(f"Quantity for {M.MENU_BY_SKU[n['sku']]['name']} must be between 1 and 24")
    return retail, list(menu.values())


def active_combos(menu_lines: list[dict]) -> list[dict]:
    groups = {M.MENU_BY_SKU[l["sku"]]["group"] for l in menu_lines}
    return [c for c in M.COMBOS if set(c["groups"]) <= groups]


def price(ctx: dict, lines) -> list[dict]:
    """Price a store cart. Retail lines go through the rules engine exactly
    as they do for the app (priced as a store pickup). Menu lines take the
    same kinds of adjustment: member tier, a fresh-batch markdown from the
    kitchen counts, the promotion for the hour, a combo - all percentages off
    the menu price, added together up to the cap."""
    retail, menu = split(lines)
    out = []
    tier = C.TIER_BY_CODE[ctx["tier"]]
    cap = ctx["pricing"].get("max_discount_pct", C.MAX_DISCOUNT_PCT)
    combos = active_combos(menu)
    for n in menu:
        m = M.MENU_BY_SKU[n["sku"]]
        product = {"sku": m["sku"], "category": "cafe", "tags": m["tags"]}
        adj = []
        if tier["discount_pct"]:
            adj.append({"rule": "tier", "label": f"{tier['name']} member", "pct": tier["discount_pct"]})
        k = ctx["kitchen"].get(m["sku"], {})
        if k.get("overstock"):
            adj.append({"rule": "overstock", "label": f"Fresh batch · {k['overstock']}% off", "pct": k["overstock"]})
        windows = [w for w in ctx["pricing"].get("time_rules", [])
                   if P.window_active(w, ctx["hour"]) and P._window_matches(w, product, C.PICKUP_OPTION)]
        if windows:
            w = max(windows, key=lambda x: x["pct_off"])
            adj.append({"rule": "time", "id": w["id"], "label": w["label"], "pct": w["pct_off"]})
        mine = [c for c in combos if m["group"] in c["groups"]]
        if mine:
            c = max(mine, key=lambda x: x["pct_off"])
            adj.append({"rule": "bundle", "id": c["id"], "label": c["name"], "pct": c["pct_off"]})
        pct = min(cap, sum(a["pct"] for a in adj))
        base = M.base_price(n["sku"], n["size"], n["mods"])
        unit = round(base * (1 - pct / 100.0), 2)
        out.append({"sku": m["sku"], "name": M.describe(n["sku"], n["size"], n["mods"]), "category": "cafe", "uom": "each",
                    "qty": n["qty"], "list_price": base, "unit_price": unit, "line_total": round(unit * n["qty"], 2),
                    "savings": round((base - unit) * n["qty"], 2), "adjustments": adj, "size": n["size"], "mods": n["mods"],
                    "key": M.line_key(n["sku"], n["size"], n["mods"]), "made_to_order": True,
                    "sold_out": bool(k.get("out"))})
    if retail:
        for l in P.quote(ctx["tier"], retail, ctx["pricing"], ctx["hour"], C.PICKUP_OPTION, ctx["offers"]):
            out.append({**l, "key": l["sku"], "made_to_order": False})
    return out


def totals(shopper: dict | None, lines: list[dict]) -> dict:
    """An identified guest gets the same totals as in the app (store credit,
    points, bonus offers). A walk-up guest pays list less the public promotions."""
    if shopper:
        return A._totals(shopper, lines, C.PICKUP_OPTION)
    subtotal = round(sum(l["line_total"] for l in lines), 2)
    tax = round(subtotal * C.TAX_RATE, 2)
    applied: dict[str, float] = {}
    for l in lines:
        for a in l.get("adjustments", []):
            if a["rule"] in ("overstock", "time", "bundle") and "pct" in a:
                applied[a["label"]] = round(applied.get(a["label"], 0) + l["list_price"] * a["pct"] / 100.0 * l["qty"], 2)
    return {"subtotal_usd": subtotal, "delivery_fee_usd": 0.0, "tax_usd": tax, "total_usd": round(subtotal + tax, 2),
            "savings_usd": round(sum(l["savings"] for l in lines), 2), "store_credit_applied_usd": 0.0,
            "amount_due_usd": round(subtotal + tax, 2), "points_earned": 0, "bonus_points": 0,
            "promotions": [{"label": k, "saved_usd": v} for k, v in applied.items()], "offers_applied": [],
            "bundles_applied": sorted({a["id"] for l in lines for a in l.get("adjustments", []) if a["rule"] == "bundle"})}


def _cart_total(ctx: dict, lines: list) -> float:
    return round(sum(l["line_total"] for l in price(ctx, lines)), 2) if lines else 0.0


async def suggestions(ctx: dict, lines: list, channel: str = "drive_thru") -> list[dict]:
    """Smart merchandising for a live store cart, best first: finish a combo,
    move what the kitchen overbaked, a bigger size, what this guest is about
    to run out of at home, and the item that completes a retail bundle."""
    out = []
    retail, menu = split(lines) if lines else ({}, [])
    have = {l["sku"] for l in menu} | set(retail)
    now_total = _cart_total(ctx, lines)

    def net(extra: dict) -> float:
        return round(_cart_total(ctx, list(lines) + [extra]) - now_total, 2)

    groups = {M.MENU_BY_SKU[l["sku"]]["group"] for l in menu}
    for c in M.COMBOS:
        missing = [g for g in c["groups"] if g not in groups]
        if len(missing) != 1 or len(groups & set(c["groups"])) == 0:
            continue
        sku = M.COMBO_FILL[missing[0]]
        if ctx["kitchen"].get(sku, {}).get("out"):
            continue
        add = {"sku": sku, "qty": 1, "size": M.DEFAULT_SIZE if "sizes" in M.MENU_BY_SKU[sku] else None, "mods": []}
        cost = net(add)
        listp = M.base_price(sku, add["size"], [])
        out.append({"id": f"combo:{c['id']}", "kind": "combo", "line": add, "source": "bundle", "net_usd": cost,
                    "label": f"Make it a {c['name'].lower()}",
                    "pitch": f"Add {M.describe(sku, add['size']).lower()} and it's a {c['name'].lower()}: {c['pct_off']}% off all three, "
                             + (f"so it's only {money(cost)} more instead of {money(listp)}." if cost >= 0.05 else
                                "so it doesn't add anything to your total.")})
    for sku, k in ctx["kitchen"].items():
        if k.get("overstock") and sku not in have:
            add = {"sku": sku, "qty": 1, "size": None, "mods": []}
            out.append({"id": f"kitchen:{sku}", "kind": "kitchen", "line": add, "source": "deal", "net_usd": net(add),
                        "label": f"Fresh batch: {M.MENU_BY_SKU[sku]['name']}",
                        "pitch": f"We just pulled a fresh batch of {M.MENU_BY_SKU[sku]['name'].lower()}s, {k['overstock']}% off right now. "
                                 f"Add one for {money(net(add))}?"})
    for l in menu:
        m = M.MENU_BY_SKU[l["sku"]]
        if "sizes" in m and l["size"] != "large" and m["group"] in ("coffee", "drink"):
            bigger = [dict(x, size="large") if x is l else x for x in menu] + [{"sku": k, "qty": v} for k, v in retail.items()]
            cost = round(_cart_total(ctx, bigger) - now_total, 2)
            out.append({"id": f"upsize:{l['sku']}", "kind": "upsize", "key": M.line_key(l["sku"], l["size"], l["mods"]), "source": "upsell",
                        "net_usd": cost, "label": f"Large {m['name']}",
                        "pitch": f"Make the {m['name'].lower()} a large for {money(cost)} more?"})
            break
    shopper = ctx.get("shopper")
    if shopper:
        recs = await store.get("recommendations", recs_id(shopper["shopper_id"])) or {}
        inv, _ = await A.get_inventory(ctx["fc"])
        for l in sorted(recs.get("lines", []), key=lambda x: x.get("days_to_runout") if x.get("days_to_runout") is not None else 99):
            dtr = l.get("days_to_runout")
            twin = (C.UPGRADES.get(l["sku"]) or [None])[0] or next((b for b, (u, _) in C.UPGRADES.items() if u == l["sku"]), None)
            if not l.get("selected") or l["sku"] in have or twin in have or dtr is None or dtr > 3:
                continue
            on_hand = int(inv["on_hand"].get(l["sku"], 0))
            if on_hand < 1:
                continue
            add = {"sku": l["sku"], "qty": 1}
            name = A.short(l["name"])
            when = "you're probably out of" if dtr <= 0 else ("you're about a day from running out of" if dtr <= 1 else
                                                           f"you're about {dtr} days from running out of")
            scarce = f" There's only {on_hand} left here." if on_hand <= 2 else ""
            aisle = l.get("aisle") or C.aisle_for(ctx["store_id"], l["sku"])
            if channel == "self_checkout":
                pitch = f"{when[0].upper()}{when[1:]} {name} at home.{scarce} Aisle {aisle}, {money(net(add))} at your price."
            else:
                where = "I can have it brought out with your order" if channel == "drive_thru" else "I can add it to your pickup"
                pitch = f"By the way, {when} {name}.{scarce} {where} for {money(net(add))}."
            out.append({"id": f"runlow:{l['sku']}", "kind": "run_low", "line": add, "source": "smart_cart", "net_usd": net(add),
                        "aisle": aisle, "label": f"Running low: {name}", "pitch": pitch})
            break
    if retail:
        aisles = (await store.get("stores", f"store::{ctx['store_id']}") or {}).get("aisles") or {}
        for b in ctx["pricing"].get("bundles", []):
            missing = [s for s in b["skus"] if s not in retail]
            if not b.get("active") or len(missing) != 1 or len(b["skus"]) < 2:
                continue
            sku = missing[0]
            add = {"sku": sku, "qty": 1}
            cost = net(add)
            listp = P.unit_price(ctx["tier"], sku, ctx["pricing"], ctx["hour"], ctx["offers"])
            saves = round(listp - cost, 2)
            if saves <= 0:
                continue
            aisle = aisles.get(sku) or C.aisle_for(ctx["store_id"], sku)
            out.append({"id": f"bundle:{b['id']}", "kind": "bundle", "line": add, "source": "bundle", "net_usd": cost, "saves_usd": saves,
                        "aisle": aisle, "label": f"Finish the {b['name']} bundle",
                        "pitch": f"Add {A.short(C.PRODUCT_BY_SKU[sku]['name'])} (aisle {aisle}) to finish the {b['name']} bundle "
                                 f"and save {money(saves)}."})
    return out


def describe_lines(lines: list[dict]) -> str:
    return ", ".join(f"{l['qty']} {A.short(l['name'])}" for l in lines)


# --------------------------------------------------------------------------
# tools (registered in actions.TOOLS, served by the MCP server, governed by AOM)
# --------------------------------------------------------------------------
async def identify_guest(shopper_id: str = "", member_number: str = "", store_id: str = "") -> dict:
    """Who is at the lane, on the phone or at the kiosk, and what the store
    should know about them: tier, wallet, their usual, what they are about to
    run out of, and their offers."""
    if not shopper_id and member_number:
        idx = await store.get("lookups", f"login::{str(member_number).strip()}")
        shopper_id = (idx or {}).get("shopper_id", "")
    s = await store.get("shoppers", f"shopper::{shopper_id}") if shopper_id else None
    if not s:
        return {"identified": False}
    if not await store.get("recommendations", recs_id(shopper_id)):
        from . import personalization       # not pre-built for a guest who hasn't opened the app lately
        await personalization.refresh_recommendations(shopper_id)
    metrics.STATS["warehouse_queries_avoided"] += 2   # profile + recommendations, read from Couchbase
    metrics.STATS["model_serving_calls_avoided"] += 1
    sid = store_id or (s.get("home_store") or {}).get("store_id") or ""
    usual = M.usual_for(shopper_id)
    out = {"identified": True, "shopper_id": shopper_id, "first_name": s["first_name"], "last_name": s["last_name"],
           "member_number": s["member_number"], "tier": s["membership"]["tier"], "tier_name": s["membership"]["tier_name"],
           "wallet": A.wallet_view(s), "home_store": s.get("home_store"), "segment": s["ml_scores"].get("segment"),
           "offers": A.shopper_offers(s), "usual": usual,
           "usual_text": " and ".join(f"{l['qty']} {M.describe(l['sku'], l.get('size'), l['mods'])}" if l["qty"] > 1 else
                                      f"a {M.describe(l['sku'], l.get('size'), l['mods'])}" for l in usual)}
    if sid in C.STORES:
        ctx = await context(sid, s)
        out["usual_total_usd"] = totals(s, price(ctx, usual))["total_usd"]
        out["run_low"] = [x for x in await suggestions(ctx, usual) if x["kind"] == "run_low"]
    return out


async def place_pos_order(shopper_id: str = "", store_id: str = "", channel: str = "drive_thru", lines: list | None = None,
                          payment: str = "", lane: str = "", session_id: str = "", notes: str = "", sources: dict | None = None,
                          pricing_version: str = "", priced_hour: int | None = None) -> dict:
    """Ring up a store order taken by the lane, the phone line or a kiosk."""
    if channel not in CHANNELS:
        raise A.ToolError(f"Channel must be one of: {', '.join(CHANNELS)}")
    st = _store(store_id)
    s = await A.load_shopper(shopper_id) if shopper_id else None
    ctx = await context(store_id, s, pricing_version, priced_hour)
    ls = price(ctx, lines or [])
    out_of = [l for l in ls if l.get("sold_out")]
    if out_of:
        return {"ok": False, "status": "failed", "reason_code": "sold_out",
                "summary": f"The kitchen is out of {M.MENU_BY_SKU[out_of[0]['sku']]['name']}. Take it off to place the rest of the order."}
    for l in ls:
        l["source"] = (sources or {}).get(l["key"]) or (sources or {}).get(l["sku"]) or "browse"
    if s:
        over = A.limit_check(s, [l for l in ls if not l["made_to_order"]])
        if over:
            o = over[0]
            return {"ok": False, "status": "failed", "reason_code": "purchase_limit", "purchase_limits": over,
                    "summary": f"{A.short(o['name'])} is limited to {o['limit']} per household every {o['window_days']} days, and "
                               f"this household bought {o['already_bought']} in that window. Remove it to pay for the rest."}
    tot = totals(s, ls)

    fc = ctx["fc"]
    retail = {l["sku"]: l["qty"] for l in ls if not l["made_to_order"]}
    got = await A.allocate_inventory(fc, retail) if retail else {}
    backordered = []
    for l in ls:
        if l["made_to_order"] or channel == "self_checkout":   # at a kiosk the item is in the guest's hands
            l["shipped_qty"], l["backordered_qty"] = l["qty"], 0
            continue
        l["shipped_qty"] = got[l["sku"]]
        l["backordered_qty"] = l["qty"] - got[l["sku"]]
        if l["backordered_qty"]:
            backordered.append({"sku": l["sku"], "name": l["name"], "qty": l["backordered_qty"],
                                "eta": (today() + dt.timedelta(days=5 + A._h(fc, l["sku"]) % 6)).isoformat()})
    state = await get_state(store_id)
    for l in ls:
        if l["sku"] in state["kitchen"]:
            k = state["kitchen"][l["sku"]]
            k["on_hand"] = max(0, k["on_hand"] - l["qty"])
    await save_state(state)

    prep = sum(M.MENU_BY_SKU[l["sku"]]["prep_seconds"] for l in ls if l["made_to_order"])
    ready = max(2, round(prep / 90)) if prep else 0
    order_id = f"PS-{today():%y%m%d}-{A._h(store_id, shopper_id, now_iso(), uuid.uuid4().hex) % 90000 + 10000}"
    status = "partially_shipped" if backordered else "confirmed"
    order = {"order_id": order_id, "date": today().isoformat(), "placed_at": now_iso(), "status": status, "channel": channel,
             "channel_name": CHANNELS[channel], "store_id": store_id, "lane": lane or None, "session_id": session_id or None,
             "delivery_option": C.PICKUP_OPTION, "eta": today().isoformat(), "fulfillment_center": fc, "pickup_store": st["name"],
             "payment": payment or ((s or {}).get("wallet") or {}).get("payment_method") or "Pay at the window",
             "notes": (notes or "").strip()[:300], "lines": ls, "line_count": len(ls), **tot, "backordered": backordered,
             "ready_in_minutes": ready, "pickup_code": f"{A._h(order_id) % 9000 + 1000}" if channel == "phone" else None,
             "pricing_version": ctx["pricing"]["version"], "priced_hour": ctx["hour"],
             "price_locked": bool(pricing_version) and ctx["pricing"]["version"] == pricing_version}
    if s:
        w = s["wallet"]
        w["store_credit_usd"] = round(float(w.get("store_credit_usd", 0)) - tot["store_credit_applied_usd"], 2)
        w["points"] = int(w.get("points", 0)) + tot["points_earned"]
        s["order_history"] = ([order] + s.get("order_history", []))[:24]
        for l in ls:
            if not l["made_to_order"] and C.PRODUCT_BY_SKU[l["sku"]]["consumable"]:
                r = s.setdefault("replenishment", {}).setdefault(l["sku"], {"avg_monthly_qty": l["qty"]})
                r["last_ordered"] = today().isoformat()
                r["last_qty"] = l["qty"]
        sp = s.setdefault("spend", {})
        sp["ytd_usd"] = round(sp.get("ytd_usd", 0) + tot["total_usd"], 2)
        sp["orders_last_12mo"] = sp.get("orders_last_12mo", 0) + 1
        once = {o["offer_id"] for o in A.shopper_offers(s) if o.get("once")}
        s["redeemed_offers"] = sorted(set(s.get("redeemed_offers", [])) | (once & set(tot["offers_applied"])))
        await A.save_shopper(s)
    metrics.merchandising(order)
    metrics.pos_order(order)
    if retail:
        await P.refresh(fc, f"store order {order_id}")
    if s:
        await A.after_change(shopper_id)   # their phone: wallet, history, and a smart cart without what they just bought

    who = f"{s['first_name']}, " if s else ""
    saved = f" You saved {money(tot['savings_usd'])}." if tot["savings_usd"] else ""
    pts = f" {tot['points_earned']} points earned." if tot["points_earned"] else ""
    credit = f" {money(tot['store_credit_applied_usd'])} store credit applied." if tot["store_credit_applied_usd"] else ""
    bo = (" Not on the shelf today, shipping to you instead: "
          + "; ".join(f"{A.short(b['name'])} x{b['qty']} (by {b['eta']})" for b in backordered) + ".") if backordered else ""
    if channel == "drive_thru":
        tail = f"Pull forward to the window{', about ' + str(ready) + ' minutes' if ready else ''}."
    elif channel == "phone":
        tail = f"Ready at {st['name']} in about {max(ready, 5)} minutes. Pickup code {order['pickup_code']}."
    else:
        tail = "Thanks for shopping with us. Your receipt is in the app." if s else "Thanks for shopping with us."
    return {"ok": True, "status": status, "charge_usd": tot["amount_due_usd"], "item": order,
            "summary": f"{who}{'order' if who else 'Order'} {order_id}: {len(ls)} item{'s' if len(ls) > 1 else ''}, {money(tot['total_usd'])}."
                       f"{saved}{credit}{pts}{bo} {tail}"}


# --------------------------------------------------------------------------
# tickets and the store's WAN link
# --------------------------------------------------------------------------
def _kick_worker():
    from . import worker
    try:
        asyncio.get_running_loop().create_task(worker.run_once())
    except RuntimeError:
        pass


async def create_ticket(store_id: str, channel: str, shopper_id: str, args: dict, title: str, agent: str, priced: list[dict],
                        tot: dict) -> tuple[str, dict]:
    state = await get_state(store_id)
    key = f"ticket::{store_id}::{uuid.uuid4().hex[:12]}"
    on_edge = state["wan"] != "up"
    doc = {"type": "request", "origin": "pos", "tool": "place_pos_order", "agent": agent, "title": title,
           "args": {**args, "store_id": store_id, "channel": channel}, "shopper_id": shopper_id or "", "store_id": store_id,
           "channel": channel, "status": "on_edge" if on_edge else "pending", "queued_on_edge": on_edge,
           "created_at": now_iso(), "edge_total_usd": tot["total_usd"],
           "kitchen": [{"qty": l["qty"], "name": l["name"]} for l in priced if l["made_to_order"]],
           "bag": [{"qty": l["qty"], "name": A.short(l["name"])} for l in priced if not l["made_to_order"]],
           "channels": [store_channel(store_id)] + ([shopper_channel(shopper_id)] if shopper_id else [])}
    await store.upsert("orders", key, doc)
    state["tickets"] = ([key] + state.get("tickets", []))[:MAX_TICKETS]
    await save_state(state)
    metrics.pos_ticket(doc)
    if not on_edge:
        _kick_worker()
    return key, doc


async def set_wan(store_id: str, up: bool) -> dict:
    """Cut or restore the store's WAN link. Restoring it releases every
    ticket the store took while it was down."""
    state = await get_state(store_id)
    state["wan"] = "up" if up else "down"
    await save_state(state)
    released = 0
    if up:
        for key in state.get("tickets", []):
            doc = await store.get("orders", key)
            if doc and doc.get("status") == "on_edge":
                doc.update(status="pending", released_at=now_iso())
                await store.upsert("orders", key, doc)
                released += 1
        if released:
            _kick_worker()
    return {"store_id": store_id, "wan": state["wan"], "released": released}


async def set_kitchen(store_id: str, sku: str, on_hand: int) -> dict:
    state = await get_state(store_id)
    if sku not in state["kitchen"]:
        raise A.ToolError("That item isn't counted by the kitchen")
    state["kitchen"][sku]["on_hand"] = max(0, int(on_hand))
    await save_state(state)
    return state


async def reset(store_id: str) -> dict:
    state = await get_state(store_id)
    state.update(wan="up", kitchen=default_kitchen(), tickets=[])
    await save_state(state)
    return state


async def store_view(store_id: str) -> dict:
    """What the store's screens show: the link, the kitchen, the tickets."""
    state = await get_state(store_id)
    tickets = []
    for key in state.get("tickets", [])[:20]:
        d = await store.get("orders", key)
        if d:
            item = d.get("item") or {}
            tickets.append({"id": key, "title": d.get("title"), "channel": d.get("channel"), "status": d.get("status"),
                            "queued_on_edge": d.get("queued_on_edge"), "kitchen": d.get("kitchen"), "bag": d.get("bag"),
                            "created_at": d.get("created_at"), "summary": d.get("summary"), "order_id": item.get("order_id"),
                            "total_usd": item.get("total_usd") or d.get("edge_total_usd"), "governed": d.get("governed"),
                            "trace_id": d.get("trace_id"), "shopper_id": d.get("shopper_id") or None,
                            "reason_code": d.get("reason_code"), "agent": d.get("agent")})
    rules = kitchen_rules(state)
    return {"store_id": store_id, "store_name": state["store_name"], "wan": state["wan"], "export_date": STATE.get("export_date"),
            "kitchen": [{"sku": sku, "name": M.MENU_BY_SKU[sku]["name"], **k, **rules.get(sku, {})} for sku, k in state["kitchen"].items()],
            "tickets": tickets, "on_edge": sum(1 for t in tickets if t["status"] == "on_edge")}
