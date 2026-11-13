"""The self-checkout vision agent: recognizes what a guest puts on the kiosk
so they don't hunt for barcodes, and catches the scans that don't add up.

The demo has no camera. A kiosk event says which item was really placed and
how well the camera could see it; from there the pipeline is the one a real
kiosk runs:

1. **Embed.** The frame becomes a vector (here: the item's reference vector
   plus noise that grows as the view gets worse).
2. **Vector search.** Nearest neighbors among the catalog's reference
   vectors, which live in one document (`vision::v1`) the kiosk holds, so
   recognition needs no WAN. The search here is brute-force cosine in
   Python over that document; in production it is a Couchbase vector index
   (Couchbase Lite vector search on the kiosk itself).
3. **Decide.** A confident match with a clear margin is added with no scan.
   Lookalikes (the 72-count and the 120-count box of the same coffee pods)
   are settled by the bagging scale. If camera and scale can't settle it the
   guest picks from the top matches, and a view too poor for that falls
   back to the barcode.
4. **Cross-check.** A scanned barcode that disagrees with what the camera
   and the scale both see (a cheaper item's barcode on a dearer item), or
   an item that reaches the bagging area unscanned and stays unconfirmed, is
   an *exception*: checkout pauses, and `resolve_checkout_exception` -
   behind AOM's human-approval tier - puts the decision with the attendant.

Recognized guests get the same member price, offers and bundle prompts as in
the app, with the aisle to walk to. Paying creates a ticket (pos.py) that
runs `place_pos_order` through AOM as the *Self-Checkout Vision Agent*.
"""
from __future__ import annotations

import logging
import math
import random
import time
import uuid

from . import actions as A
from . import catalog as C
from . import metrics, pos, tool_gateway
from .docs import money, now_iso
from .store import store

log = logging.getLogger("pos.vision")

DIM = 24
AUTO_SCORE = 0.80          # at or above: confident on the camera alone...
AUTO_MARGIN = 0.08         # ...when the runner-up is at least this far behind
MIN_SCORE = 0.55           # below: don't guess, ask for the barcode
WEIGHT_TOLERANCE = 0.05    # the bagging scale must agree within 5%
NOISE = {"clear": 0.035, "occluded": 0.15, "poor": 0.80}
SESSION_TTL_SECONDS = 2 * 3600
INDEX_ID = "vision::v1"
DECISIONS = ("charge_as_seen", "accept_scan", "remove")

_SESSIONS: dict[str, dict] = {}


def _rand_unit(seed: str, dim: int = DIM) -> list[float]:
    r = random.Random(seed)
    return _unit([r.gauss(0, 1) for _ in range(dim)])


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _families() -> dict[str, str]:
    """Value sizes and pro models look like the item they step up from."""
    fam = {p["sku"]: p["sku"] for p in C.PRODUCTS}
    for base, (up, _) in C.UPGRADES.items():
        fam[up] = base
    return fam


FAMILY = _families()


def _embedding(sku: str) -> list[float]:
    p = C.PRODUCT_BY_SKU[sku]
    fam, brand, own = _rand_unit(f"family|{FAMILY[sku]}"), _rand_unit(f"brand|{p['name'].split()[0]}"), _rand_unit(f"sku|{sku}")
    return _unit([0.80 * f + 0.30 * b + 0.22 * o for f, b, o in zip(fam, brand, own)])


def _weight(sku: str) -> int:
    base = FAMILY[sku]
    w = 200 + A._h("weight", base) % 2300
    return w if base == sku else round(w * 1.55)


EMBEDDINGS = {p["sku"]: _embedding(p["sku"]) for p in C.PRODUCTS}
WEIGHT_G = {p["sku"]: _weight(p["sku"]) for p in C.PRODUCTS}


def index_doc() -> dict:
    return {"type": "vision_index", "model": "shopsmart-shelf-embed-v1 (simulated)", "dim": DIM, "metric": "cosine",
            "items": {sku: {"vector": [round(x, 5) for x in v], "weight_g": WEIGHT_G[sku]} for sku, v in EMBEDDINGS.items()},
            "channels": ["store-devices"], "generated_at": now_iso()}


async def publish_index():
    if not await store.get("catalog", INDEX_ID):
        await store.upsert("catalog", INDEX_ID, index_doc())


def search(vector: list[float], k: int = 3) -> list[dict]:
    """Cosine nearest neighbors over the catalog's reference vectors."""
    scored = sorted(((sum(a * b for a, b in zip(vector, ref)), sku) for sku, ref in EMBEDDINGS.items()), reverse=True)[:k]
    return [{"sku": sku, "name": A.short(C.PRODUCT_BY_SKU[sku]["name"]), "pack": C.PRODUCT_BY_SKU[sku]["pack"],
             "score": round(score, 3), "weight_g": WEIGHT_G[sku]} for score, sku in scored]


def capture(true_sku: str, view: str, n: int = 0) -> tuple[list[float], int]:
    """What the kiosk's sensors report for an item: an embedding of the frame
    and the bagging scale's reading. Seeded by the item, the view and how many
    times it has been placed, so the demo plays the same way every time."""
    r = random.Random(f"{true_sku}|{view}|{n}")
    sigma = NOISE.get(view, NOISE["clear"])
    vec = _unit([x + r.gauss(0, sigma) for x in EMBEDDINGS[true_sku]])
    grams = round(WEIGHT_G[true_sku] * (1 + r.uniform(-0.012, 0.012)))
    return vec, grams


def _weight_ok(sku: str, grams: int | None) -> bool:
    return grams is not None and abs(grams - WEIGHT_G[sku]) / WEIGHT_G[sku] <= WEIGHT_TOLERANCE


def decide(cands: list[dict], grams: int | None, barcode: str | None) -> dict:
    """The agent's decision for one item. Pure: same inputs, same answer."""
    top, second = cands[0], cands[1]
    margin = round(top["score"] - second["score"], 3)
    by_scale = [c for c in cands if c["score"] >= MIN_SCORE and _weight_ok(c["sku"], grams)]
    seen = by_scale[0]["sku"] if len(by_scale) == 1 else (
        top["sku"] if top["score"] >= AUTO_SCORE and margin >= AUTO_MARGIN and grams is None else None)
    base = {"top": top, "margin": margin, "candidates": cands, "scale_g": grams}
    if barcode:
        if seen and seen != barcode and not _weight_ok(barcode, grams):
            return {**base, "action": "exception", "kind": "scan_mismatch", "sku": barcode, "seen": seen,
                    "why": "The barcode says one item; the camera and the scale agree on another."}
        return {**base, "action": "add", "sku": barcode, "method": "barcode",
                "why": "Scanned" + (" · camera agrees" if seen == barcode or top["sku"] == barcode else "")}
    if top["score"] >= AUTO_SCORE and margin >= AUTO_MARGIN and (grams is None or _weight_ok(top["sku"], grams)):
        return {**base, "action": "add", "sku": top["sku"], "method": "camera",
                "why": f"Recognized · {round(top['score'] * 100)}% match, next best {round(second['score'] * 100)}%"}
    if len(by_scale) == 1:
        why = (f"Two lookalikes on camera ({round(top['score'] * 100)}% vs {round(second['score'] * 100)}%); "
               f"the scale read {grams} g, which only fits one" if margin < AUTO_MARGIN else
               f"Partly hidden on camera ({round(top['score'] * 100)}% match); the scale read {grams} g, which confirms it")
        return {**base, "action": "add", "sku": by_scale[0]["sku"], "method": "camera + scale", "why": why}
    if grams is not None and top["score"] >= MIN_SCORE:
        return {**base, "action": "scan", "why": f"The camera's best match ({round(top['score'] * 100)}%) doesn't fit the {grams} g on the scale: not guessing"}
    if top["score"] >= MIN_SCORE:
        return {**base, "action": "ask", "options": [c for c in cands if c["score"] >= MIN_SCORE] if second["score"] >= MIN_SCORE else cands[:2],
                "why": "The camera can't tell these apart" + ("" if grams is not None else " and the item isn't on the scale")}
    return {**base, "action": "scan", "why": f"Best match is only {round(top['score'] * 100)}%: not guessing"}


# --------------------------------------------------------------------------
# kiosk sessions
# --------------------------------------------------------------------------
async def _save(sess: dict):
    sess["updated_at"] = now_iso()
    _SESSIONS[sess["session_id"]] = sess
    await store.upsert("lookups", f"kiosk::{sess['session_id']}", sess, ttl=SESSION_TTL_SECONDS)


async def load(session_id: str) -> dict:
    # The copy in the store wins: an approved exception is settled there by the MCP tool.
    sess = await store.get("lookups", f"kiosk::{session_id}") or _SESSIONS.get(session_id)
    if not sess:
        raise ValueError("That checkout session has ended")
    return sess


def _log(sess: dict, kind: str, text: str, **extra):
    sess["events"] = ([{"at": now_iso(), "kind": kind, "text": text, **extra}] + sess.get("events", []))[:40]


async def _shopper(sess: dict) -> dict | None:
    return await store.get("shoppers", f"shopper::{sess['shopper_id']}") if sess.get("shopper_id") else None


async def start(store_id: str, kiosk: str = "", shopper_id: str = "", member_number: str = "") -> dict:
    if store_id not in C.STORES:
        raise ValueError("Unknown store")
    await publish_index()
    sess = {"type": "kiosk_session", "session_id": uuid.uuid4().hex[:12], "store_id": store_id, "store_name": C.STORES[store_id]["name"],
            "kiosk": kiosk or "Kiosk 3", "state": "scanning", "shopper_id": "", "guest": None, "items": [], "seq": 0, "placed": {},
            "pending": None,
            "exceptions": [], "events": [], "ticket_id": None, "started_at": now_iso(), "started_ts": time.time()}
    metrics.bump("kiosk_sessions")
    _log(sess, "info", "Checkout started. Place items on the tray; no need to find the barcode.")
    if shopper_id or member_number:
        await _loyalty(sess, shopper_id, member_number)
    await _save(sess)
    return await view(sess)


async def _loyalty(sess: dict, shopper_id: str = "", member_number: str = "") -> bool:
    who = await pos.identify_guest(shopper_id=shopper_id, member_number=member_number, store_id=sess["store_id"])
    if not who.get("identified"):
        _log(sess, "warn", "That member number wasn't found. Continuing at regular prices.")
        return False
    sess.update(shopper_id=who["shopper_id"],
                guest={k: who.get(k) for k in ("first_name", "last_name", "tier_name", "wallet", "segment", "offers", "member_number")})
    _log(sess, "ok", f"Welcome back, {who['first_name']}. {who['tier_name']} prices and your offers are on this basket.")
    return True


def _add_item(sess: dict, sku: str, method: str, why: str, score: float | None = None, source: str | None = None) -> dict:
    sess["seq"] += 1
    item = {"seq": sess["seq"], "sku": sku, "name": A.short(C.PRODUCT_BY_SKU[sku]["name"]), "method": method, "why": why,
            "score": score, "source": source or ("scan" if method == "barcode" else "vision")}
    sess["items"].append(item)
    return item


def _exception(sess: dict, kind: str, **details) -> dict:
    exc = {"id": f"X{len(sess['exceptions']) + 1}-{uuid.uuid4().hex[:4]}", "kind": kind, "status": "open", "raised_at": now_iso(), **details}
    sess["exceptions"].append(exc)
    metrics.bump("kiosk_exceptions")
    return exc


async def event(session_id: str, kind: str, data: dict) -> dict:
    sess = await load(session_id)
    if sess["state"] == "paid":
        raise ValueError("This checkout is finished")
    data = data or {}
    if kind == "loyalty":
        await _loyalty(sess, data.get("shopper_id") or "", str(data.get("member_number") or ""))
    elif kind == "place":
        await _place(sess, data)
    elif kind == "bag":
        sku = data.get("sku")
        if sku not in C.PRODUCT_BY_SKU:
            raise ValueError("Unknown item")
        vec, grams = capture(sku, "clear", 100 + sess["placed"].get(sku, 0))
        seen = decide(search(vec), grams, None)
        sess["pending"] = {"kind": "missed", "sku": seen.get("sku") or sku, "scale_g": grams,
                           "ask": f"It looks like {A.short(C.PRODUCT_BY_SKU[seen.get('sku') or sku]['name'])} went into the bag without "
                                  f"being rung up. Add it?"}
        _log(sess, "warn", f"Bagging camera: an item reached the bag unscanned ({grams} g).")
    elif kind == "answer":
        await _answer(sess, data)
    elif kind == "remove":
        hit = next((i for i in reversed(sess["items"]) if i["sku"] == data.get("sku")), None)
        if hit:
            sess["items"].remove(hit)
            _log(sess, "info", f"Removed {hit['name']}.")
    elif kind == "add_suggestion":
        ctx = await pos.context(sess["store_id"], await _shopper(sess))
        sug = next((s for s in await _suggestions(sess, ctx) if s["id"] == data.get("id")), None)
        if not sug:
            raise ValueError("That suggestion is no longer available")
        _add_item(sess, sug["line"]["sku"], "barcode", "Added from a suggestion on the kiosk", source=sug["source"])
        metrics.bump("kiosk_items")
        _log(sess, "ok", f"Added {A.short(C.PRODUCT_BY_SKU[sug['line']['sku']]['name'])}: {sug['label']}.")
    elif kind == "pay":
        await _pay(sess)
    else:
        raise ValueError("Unknown kiosk event")
    await _save(sess)
    return await view(sess)


async def _place(sess: dict, data: dict):
    sku, barcode = data.get("sku"), data.get("barcode") or None
    if sku not in C.PRODUCT_BY_SKU or (barcode and barcode not in C.PRODUCT_BY_SKU):
        raise ValueError("Unknown item")
    if sess.get("pending"):
        raise ValueError("Answer the question on the screen first")
    view_q = data.get("view") if data.get("view") in NOISE else "clear"
    t0 = time.perf_counter()
    vec, grams = capture(sku, view_q, sess["placed"].get(sku, 0))
    sess["placed"][sku] = sess["placed"].get(sku, 0) + 1
    if data.get("on_scale") is False:
        grams = None
    cands = search(vec)
    d = decide(cands, grams, barcode)
    d["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    metrics.bump("kiosk_items")
    metrics.STATS["model_serving_calls_avoided"] += 1     # the lookup ran on the kiosk, not against a hosted endpoint
    sess["last"] = {"view": view_q, "barcode": barcode, **{k: d.get(k) for k in ("action", "method", "why", "candidates", "margin", "scale_g", "ms")},
                    "sku": d.get("sku")}
    if d["action"] == "add":
        _add_item(sess, d["sku"], d["method"], d["why"], d["top"]["score"])
        if d["method"] == "camera":
            metrics.bump("kiosk_auto_recognized")
        elif d["method"] == "camera + scale":
            metrics.bump("kiosk_resolved_by_scale")
        _log(sess, "ok", f"{A.short(C.PRODUCT_BY_SKU[d['sku']]['name'])} · {d['why']}.", method=d["method"])
    elif d["action"] == "ask":
        metrics.bump("kiosk_asked_guest")
        sess["pending"] = {"kind": "which", "options": d["options"], "ask": "Which one is this?", "scale_g": grams, "sku": sku}
        _log(sess, "warn", f"{d['why']}. Asking the guest.")
    elif d["action"] == "scan":
        metrics.bump("kiosk_barcode_fallback")
        sess["pending"] = {"kind": "scan", "ask": "I couldn't see that one clearly. Please scan its barcode.", "sku": sku}
        _log(sess, "warn", f"{d['why']}. Asking for the barcode.")
    else:
        ctx = await pos.context(sess["store_id"], await _shopper(sess))
        scanned_price = pos.price(ctx, [{"sku": barcode, "qty": 1}])[0]["unit_price"]
        seen_price = pos.price(ctx, [{"sku": d["seen"], "qty": 1}])[0]["unit_price"]
        item = _add_item(sess, barcode, "barcode", "Scanned · on hold", d["top"]["score"])
        item["held"] = True
        exc = _exception(sess, "scan_mismatch", seq=item["seq"], scanned=barcode, seen=d["seen"],
                         scanned_name=A.short(C.PRODUCT_BY_SKU[barcode]["name"]) + f" ({C.PRODUCT_BY_SKU[barcode]['pack']})",
                         seen_name=A.short(C.PRODUCT_BY_SKU[d["seen"]]["name"]) + f" ({C.PRODUCT_BY_SKU[d['seen']]['pack']})",
                         scanned_price=scanned_price, seen_price=seen_price, difference_usd=round(seen_price - scanned_price, 2),
                         evidence=f"Camera: {round(d['top']['score'] * 100)}% match to the item seen. Scale: {grams} g; the scanned "
                                  f"item weighs {WEIGHT_G[barcode]} g, the item seen {WEIGHT_G[d['seen']]} g.")
        _log(sess, "bad", f"Scan doesn't match the item: scanned {exc['scanned_name']}, seeing {exc['seen_name']}. "
                          f"An attendant has been called.", exception_id=exc["id"])


async def _answer(sess: dict, data: dict):
    pending, sess["pending"] = sess.get("pending"), None
    if not pending:
        raise ValueError("Nothing to answer")
    choice = str(data.get("choice") or "")
    if pending["kind"] == "which" and choice == "none":
        sess["pending"] = {"kind": "scan", "ask": "No problem. Please scan its barcode.", "sku": pending.get("sku")}
        metrics.bump("kiosk_barcode_fallback")
    elif pending["kind"] == "which":
        if choice not in {o["sku"] for o in pending["options"]}:
            sess["pending"] = pending
            raise ValueError("Pick one of the options")
        _add_item(sess, choice, "guest picked", "The guest chose between the camera's top matches")
        _log(sess, "ok", f"{A.short(C.PRODUCT_BY_SKU[choice]['name'])} · picked by the guest.")
    elif pending["kind"] == "scan":
        if choice not in C.PRODUCT_BY_SKU:
            sess["pending"] = pending
            raise ValueError("Scan the barcode")
        _add_item(sess, choice, "barcode", "Scanned after the camera couldn't see it")
        _log(sess, "ok", f"{A.short(C.PRODUCT_BY_SKU[choice]['name'])} · scanned.")
    elif pending["kind"] == "missed":
        sku = pending["sku"]
        if choice == "yes":
            _add_item(sess, sku, "bagging camera", "Seen in the bagging area and confirmed by the guest", source="vision")
            ctx = await pos.context(sess["store_id"], await _shopper(sess))
            metrics.bump("kiosk_nudges_accepted")
            metrics.bump("kiosk_shrink_prevented_usd", float(pos.price(ctx, [{"sku": sku, "qty": 1}])[0]["unit_price"]))
            _log(sess, "ok", f"{A.short(C.PRODUCT_BY_SKU[sku]['name'])} · added after a reminder. No attendant needed.")
        else:
            ctx = await pos.context(sess["store_id"], await _shopper(sess))
            p = pos.price(ctx, [{"sku": sku, "qty": 1}])[0]["unit_price"]
            exc = _exception(sess, "unscanned_item", seen=sku, seen_name=A.short(C.PRODUCT_BY_SKU[sku]["name"]), seen_price=p,
                             difference_usd=p, evidence=f"Bagging camera and scale ({pending.get('scale_g')} g) see an item that "
                                                        f"isn't on the receipt, and the guest declined to add it.")
            _log(sess, "bad", f"Unscanned item in the bag: {exc['seen_name']}. An attendant has been called.", exception_id=exc["id"])


async def _suggestions(sess: dict, ctx: dict) -> list[dict]:
    lines = _lines(sess)
    if not lines:
        return []
    return [s for s in await pos.suggestions(ctx, lines, "self_checkout") if s["kind"] in ("bundle", "run_low")]


def _lines(sess: dict) -> list[dict]:
    counts: dict[str, int] = {}
    for i in sess["items"]:
        counts[i["sku"]] = counts.get(i["sku"], 0) + 1
    return [{"sku": sku, "qty": qty} for sku, qty in counts.items()]


def _blockers(sess: dict) -> list[str]:
    out = []
    if sess.get("pending"):
        out.append("Answer the question on the screen")
    if any(e["status"] != "resolved" for e in sess["exceptions"]):
        out.append("An attendant needs to clear an exception")
    if not sess["items"]:
        out.append("Nothing in the basket yet")
    return out


async def _pay(sess: dict):
    blockers = _blockers(sess)
    if blockers:
        raise ValueError(blockers[0])
    shopper = await _shopper(sess)
    ctx = await pos.context(sess["store_id"], shopper)
    lines = _lines(sess)
    priced = pos.price(ctx, lines)
    tot = pos.totals(shopper, priced)
    sources = {}
    for i in sess["items"]:
        sources.setdefault(i["sku"], i["source"])
    payment = (shopper or {}).get("wallet", {}).get("payment_method") or "Tap to pay"
    who = f"{sess['guest']['first_name']} · " if sess.get("guest") else ""
    n = len(sess["items"])
    title = f"Self-checkout · {who}{n} item{'s' if n > 1 else ''} · {money(tot['total_usd'])}"
    args = {"lines": lines, "sources": sources, "payment": payment, "lane": sess["kiosk"], "session_id": sess["session_id"],
            "pricing_version": ctx["pricing"]["version"], "priced_hour": ctx["hour"]}
    key, _ = await pos.create_ticket(sess["store_id"], "self_checkout", sess["shopper_id"], args, title, "vision", priced, tot)
    secs = round(time.time() - sess.get("started_ts", time.time()), 1)
    # What the guest paid stays on the screen: the order itself changes their wallet a moment later.
    sess.update(state="paying", ticket_id=key, checkout_seconds=secs, error=None, paid={"cart": priced, "totals": tot})
    metrics.bump("kiosk_checkout_seconds_total", float(secs))
    _log(sess, "ok", f"Paid {money(tot['amount_due_usd'])} by {payment}.")


# --------------------------------------------------------------------------
# exceptions: the vision agent asks, a person decides
# --------------------------------------------------------------------------
async def resolve_checkout_exception(shopper_id: str = "", store_id: str = "", session_id: str = "", exception_id: str = "",
                                     decision: str = "", attendant: str = "", note: str = "") -> dict:
    """The tool behind an attendant's decision. Held by AOM's approval tier
    until a person approves it; applied here."""
    try:
        sess = await load(session_id)
    except ValueError as exc:
        raise A.ToolError(str(exc)) from None
    exc = next((e for e in sess["exceptions"] if e["id"] == exception_id), None)
    if not exc or sess["store_id"] != store_id:
        raise A.ToolError(f"No exception '{exception_id}' on that checkout")
    if exc["status"] == "resolved":
        return {"ok": True, "status": "confirmed", "summary": f"Already settled: {exc.get('outcome')}"}
    if decision not in DECISIONS:
        raise A.ToolError(f"Decision must be one of: {', '.join(DECISIONS)}")
    item = next((i for i in sess["items"] if i["seq"] == exc.get("seq")), None)
    if exc["kind"] == "scan_mismatch":
        if decision == "charge_as_seen":
            item.update(sku=exc["seen"], name=A.short(C.PRODUCT_BY_SKU[exc["seen"]]["name"]), method="attendant",
                        why="Rung up as the item on the tray", held=False)
            metrics.bump("kiosk_shrink_prevented_usd", float(max(exc["difference_usd"], 0)))
            outcome = f"Rung up as {exc['seen_name']} at {money(exc['seen_price'])}"
        elif decision == "accept_scan":
            item.update(method="attendant", why="Attendant checked the item: the scan was right", held=False)
            outcome = f"Scan accepted: {exc['scanned_name']}"
        else:
            sess["items"].remove(item)
            outcome = "Item removed from the basket"
    else:
        if decision == "charge_as_seen":
            _add_item(sess, exc["seen"], "attendant", "Added by the attendant")
            metrics.bump("kiosk_shrink_prevented_usd", float(exc["seen_price"]))
            outcome = f"{exc['seen_name']} added at {money(exc['seen_price'])}"
        elif decision == "remove":
            outcome = f"{exc['seen_name']} taken back by the attendant"
        else:
            raise A.ToolError("An unscanned item is either charged or removed")
    exc.update(status="resolved", decision=decision, outcome=outcome, resolved_at=now_iso(),
               resolved_by=(attendant or "Front-end attendant").strip()[:60], note=(note or "").strip()[:200])
    metrics.bump("kiosk_exceptions_resolved")
    _log(sess, "ok", f"Exception cleared by {exc['resolved_by']}: {outcome}.")
    await _save(sess)
    return {"ok": True, "status": "confirmed", "charge_usd": 0, "item": {"kind": "checkout_exception", **exc},
            "summary": f"{exc['resolved_by']} settled the self-checkout exception at {sess['kiosk']}: {outcome}."}


def _exc_args(sess: dict, exc: dict) -> dict:
    return {"shopper_id": sess.get("shopper_id") or "", "store_id": sess["store_id"], "session_id": sess["session_id"],
            "exception_id": exc["id"], "decision": exc["requested_decision"], "attendant": exc.get("requested_by") or "",
            "note": exc.get("requested_note") or ""}


async def request_resolution(session_id: str, exception_id: str, decision: str, attendant: str = "", note: str = "") -> dict:
    """The attendant's tap. Goes through AOM as the vision agent; AOM holds
    it for approval. With the WAN down (or no AOM) it is applied at the store."""
    sess = await load(session_id)
    exc = next((e for e in sess["exceptions"] if e["id"] == exception_id), None)
    if not exc:
        raise ValueError("No such exception")
    if exc["status"] == "resolved":
        return await view(sess)
    if decision not in DECISIONS:
        raise ValueError(f"Decision must be one of: {', '.join(DECISIONS)}")
    exc.update(requested_decision=decision, requested_by=(attendant or "Front-end attendant").strip()[:60], requested_note=note)
    await _save(sess)
    state = await pos.get_state(sess["store_id"])
    res = await tool_gateway.invoke("resolve_checkout_exception", _exc_args(sess, exc), agent="vision", local=state["wan"] != "up")
    sess = await load(session_id)
    exc = next(e for e in sess["exceptions"] if e["id"] == exception_id)
    if res.get("status") == "awaiting_approval":
        exc.update(status="awaiting_approval", approval_id=res.get("approval_id"), trace_id=res.get("trace_id"))
        _log(sess, "warn", "Waiting for the attendant's approval in AOM › Approvals.")
    elif not res.get("ok"):
        _log(sess, "bad", res.get("summary") or "The exception could not be settled.")
    else:
        exc.update(governed=res.get("governed", False), trace_id=res.get("trace_id"))
    await _save(sess)
    return await view(sess)


async def _poll_approvals(sess: dict) -> bool:
    changed = False
    for exc in sess["exceptions"]:
        if exc["status"] != "awaiting_approval":
            continue
        status = await tool_gateway.approval_status(exc.get("approval_id") or "")
        if status in (None, "pending"):
            continue
        if status == "approved":
            res = await tool_gateway.invoke("resolve_checkout_exception", _exc_args(sess, exc), approval_id=exc["approval_id"], agent="vision")
            fresh = await load(sess["session_id"])
            sess.clear()
            sess.update(fresh)
            e2 = next(e for e in sess["exceptions"] if e["id"] == exc["id"])
            e2.update(governed=True, trace_id=res.get("trace_id") or e2.get("trace_id"))
        else:
            exc.update(status="open", approval_id=None)
            _log(sess, "warn", f"The approval was {status}. The exception is still open.")
        changed = True
    return changed


async def view(sess: dict) -> dict:
    if await _poll_approvals(sess):
        await _save(sess)
    shopper = await _shopper(sess)
    ctx = await pos.context(sess["store_id"], shopper)
    ticket = None
    if sess.get("ticket_id"):
        d = await store.get("orders", sess["ticket_id"]) or {}
        item = d.get("item") or {}
        ticket = {"id": sess["ticket_id"], "status": d.get("status"), "summary": d.get("summary"), "order_id": item.get("order_id"),
                  "governed": d.get("governed"), "trace_id": d.get("trace_id"), "queued_on_edge": d.get("queued_on_edge"),
                  "total_usd": item.get("total_usd") or d.get("edge_total_usd"), "points_earned": item.get("points_earned"),
                  "reason_code": d.get("reason_code")}
        if d.get("status") == "failed" and sess["state"] != "scanning":
            # A governed refusal (the purchase limit): back to the basket with the reason.
            sess.update(state="scanning", error=d.get("summary"), ticket_id=None)
            _log(sess, "bad", d.get("summary") or "The order was refused.")
            await _save(sess)
        elif d.get("status") in ("confirmed", "partially_shipped") and sess["state"] != "paid":
            sess["state"] = "paid"
            metrics.STATS["warehouse_queries_avoided"] += 1
            await _save(sess)
    lines = _lines(sess)
    priced = pos.price(ctx, lines) if lines else []
    tot = pos.totals(shopper, priced)
    if sess["state"] != "scanning" and sess.get("paid"):
        priced, tot = sess["paid"]["cart"], sess["paid"]["totals"]
    by_sku = {l["sku"]: l for l in priced}
    limits = A.limit_check(shopper, priced) if shopper and priced else []
    return {"session_id": sess["session_id"], "store_id": sess["store_id"], "store_name": sess["store_name"], "kiosk": sess["kiosk"],
            "state": sess["state"], "guest": sess.get("guest"), "shopper_id": sess.get("shopper_id") or None,
            "items": [{**i, "unit_price": by_sku[i["sku"]]["unit_price"], "list_price": by_sku[i["sku"]]["list_price"],
                       "adjustments": by_sku[i["sku"]]["adjustments"]} for i in sess["items"]],
            "cart": priced, "totals": tot, "pending": sess.get("pending"), "exceptions": sess["exceptions"],
            "blockers": _blockers(sess), "suggestions": [{k: s.get(k) for k in ("id", "kind", "label", "pitch", "net_usd", "saves_usd", "aisle")}
                                                         for s in await _suggestions(sess, ctx)] if sess["state"] == "scanning" else [],
            "purchase_limits": limits, "last": sess.get("last"), "events": sess.get("events", [])[:14], "ticket": ticket,
            "error": sess.get("error"), "wan": ctx["state"]["wan"], "checkout_seconds": sess.get("checkout_seconds"),
            "index": {"id": INDEX_ID, "dim": DIM, "items": len(EMBEDDINGS), "metric": "cosine"},
            "reads": [f"catalog::{INDEX_ID} (reference vectors)", f"pricing::{ctx['fc']} (v {ctx['pricing']['version'][:8]})",
                      f"store::{sess['store_id']} (aisles)"] + ([f"shopper::{sess['shopper_id']}", f"recs::{sess['shopper_id']}"]
                                                              if sess.get("shopper_id") else [])}


def shelf(store_id: str) -> list[dict]:
    """What the demo page lets you pick up: the catalog with aisle, weight and its lookalike."""
    twins = {}
    for base, (up, _) in C.UPGRADES.items():
        twins[base], twins[up] = up, base
    return [{"sku": p["sku"], "name": A.short(p["name"]), "pack": p["pack"], "category": C.CATEGORIES[p["category"]]["name"],
             "list_price": p["list_price"], "aisle": C.aisle_for(store_id, p["sku"]), "weight_g": WEIGHT_G[p["sku"]],
             "lookalike": twins.get(p["sku"]), "limit_per_household": p.get("limit_per_household")} for p in C.PRODUCTS]
