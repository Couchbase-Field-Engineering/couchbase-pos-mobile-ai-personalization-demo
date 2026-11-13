"""The voice ordering agent: takes orders at the drive-thru lane and on the
store's phone line.

A turn is: transcript in (speech-to-text runs on the lane device or in the
browser), a reply to speak out. In between, everything the agent needs is
already at the store:

* **Understanding.** A grammar over the menu, the modifiers and the retail
  catalog parses the order on the store edge in well under a millisecond,
  with or without a WAN link. Only an utterance it can't parse goes to an LLM,
  through AOM's LLM gateway (cached, traced, attributed to this agent).
* **Personalization.** A recognized guest (app check-in at the lane, caller
  ID on the phone, or a spoken member number) gets their usual, their member
  price, and one line about what the nightly model says they are about to
  run out of at home - read from the same `shopper::` and `recs::` documents
  their phone holds.
* **Smart merchandising.** After each item the agent may make one offer,
  chosen by pos.suggestions: finish a combo, move what the kitchen overbaked,
  a bigger size, the run-low item. Every accepted offer is tagged on the
  order line, so the lift is measured the same way as in the app.
* **The order.** Confirmed orders become tickets (pos.create_ticket): the
  kitchen sees them at once, and the worker runs `place_pos_order` through
  AOM as the *ShopSmart POS Voice Ordering Agent*.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid

from . import actions as A
from . import catalog as C
from . import config, metrics
from . import menu as M
from . import pos
from . import pricing as P
from .aom_client import AOMError, aom, current_agent
from .docs import money, now_iso
from .store import store

log = logging.getLogger("pos.voice")

SESSION_TTL_SECONDS = 2 * 3600
MAX_OFFERS = 3
OFFER_ORDER = {"kitchen": 0, "combo": 1, "run_low": 2, "upsize": 3}

NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "couple": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "dozen": 12, "zero": 0, "oh": 0}
# What guests call the everyday retail items they add to a pickup.
RETAIL_SAY = {
    "SS-110245": ["coffee pods", "k cups", "pods"], "SS-110260": ["value box of coffee pods", "big box of coffee pods"],
    "SS-110388": ["rolled oats", "oatmeal", "oats"], "SS-110512": ["olive oil"], "SS-110630": ["penne", "pasta"],
    "SS-110777": ["sparkling water", "seltzer"], "SS-110903": ["trail mix"], "SS-111046": ["fruit pouches"],
    "SS-220134": ["paper towels"], "SS-220268": ["bath tissue", "toilet paper"], "SS-220391": ["laundry detergent", "detergent"],
    "SS-220455": ["dish soap"], "SS-220579": ["dishwasher pods"], "SS-220642": ["trash bags", "garbage bags"],
    "SS-220788": ["disinfecting wipes", "cleaning wipes"], "SS-330117": ["toothpaste"], "SS-330254": ["shampoo"],
    "SS-330396": ["body wash"], "SS-330431": ["sunscreen"], "SS-330570": ["multivitamins", "vitamins"],
    "SS-440129": ["diapers"], "SS-440263": ["baby wipes", "wipes"], "SS-440377": ["toddler formula", "formula"],
    "SS-550118": ["dog food"], "SS-550246": ["cat food"], "SS-550359": ["cat litter", "litter"], "SS-550472": ["dental chews", "dog treats"],
    "SS-990334": ["protein bars"],
}
YES_RE = re.compile(r"^(yes please|yes|yeah|yep|yup|sure thing|sure|ok|okay|please do|please|sounds good|sounds great|do it|add it|"
                    r"add that|why not|correct|thats right|that is right|right|go ahead|perfect|absolutely|lets do it)\b")
NO_RE = re.compile(r"^(no thanks|no thank you|not today|nope|nah|no)\b")
DONE_RE = re.compile(r"\b(thats all|that is all|thats it|that is it|thatll be all|that will be all|thatll do|nothing else|im done|"
                     r"i am done|im good|im all set|all set|thats everything|check ?out|complete (the|my) order|place (the|my) order)\b")
REMOVE_RE = re.compile(r"\b(remove|take off|take out|drop|scratch|cancel|delete|no more|lose)\b")
CLEAR_RE = re.compile(r"\b(start over|cancel (the|my|this) (whole )?order|clear (the|my) order|forget (it|the order))\b")
TOTAL_RE = re.compile(r"\b(total|how much|what do i owe|whats the damage)\b")
DEALS_RE = re.compile(r"\b(deals?|specials?|recommend|suggest|whats good|what is good|promotions?|anything on sale)\b")
READBACK_RE = re.compile(r"\b(read (it|that) back|what do i have|repeat (my|the) order|what did i order)\b")
USUAL_RE = re.compile(r"\b(the|my) usual\b|\bsame as (always|last time)\b")
MEMBER_RE = re.compile(r"(?:member(?:ship)?|rewards|loyalty)\s*(?:number|id|card)?\s*(?:is|its)?\s*((?:\d\s?){5,8})")

_SESSIONS: dict[str, dict] = {}


# --------------------------------------------------------------------------
# understanding: a grammar over the menu, the modifiers and the retail aliases
# --------------------------------------------------------------------------
def normalize(text: str) -> str:
    t = text.lower().replace("&", " and ").replace("-", " ")
    t = re.sub(r"[’']", "", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _aliases() -> list[tuple[tuple[str, ...], str, str]]:
    out = []
    for m in M.MENU:
        out += [(tuple(normalize(a).split()), "item", m["sku"]) for a in m["say"] + [m["name"]]]
    for sku, says in RETAIL_SAY.items():
        out += [(tuple(normalize(a).split()), "retail", sku) for a in says]
    for mid, mod in M.MODIFIERS.items():
        out += [(tuple(normalize(a).split()), "mod", mid) for a in mod["say"]]
    out += [((s,), "size", s) for s in M.SIZES] + [(("regular",), "size", "medium"), (("big",), "size", "large")]
    return sorted(set(out), key=lambda a: -len(a[0]))


ALIASES = _aliases()


def tag(text: str) -> list[tuple[str, object]]:
    """Longest-match tagging: [('num', 2), ('size', 'large'), ('mod', 'oat_milk'), ('item', 'CF-1001'), ('sep', 'and'), ...]"""
    toks = text.split()
    out, i = [], 0
    while i < len(toks):
        for words, kind, ident in ALIASES:
            n = len(words)
            if tuple(toks[i:i + n]) == words:
                out.append((kind, ident))
                i += n
                break
        else:
            w = toks[i]
            if w.isdigit() and int(w) <= 24:
                out.append(("num", int(w)))
            elif w in NUMBERS:
                out.append(("num", NUMBERS[w]))
            elif w in ("and", "plus", "also", "then"):
                out.append(("sep", w))
            else:
                out.append(("word", w))
            i += 1
    return out


def parse_items(text: str) -> tuple[list[dict], dict]:
    """The order lines an utterance names, and any size or modifiers said with
    no item ('make that a large', 'no cheese on that')."""
    items: list[dict] = []
    pre = {"qty": None, "size": None, "mods": []}
    after_sep = False
    for kind, val in tag(text):
        if kind == "num":
            pre["qty"] = val
            after_sep = True
        elif kind == "sep":
            after_sep = True
        elif kind in ("size", "mod"):
            target = items[-1] if items and not after_sep and items[-1]["kind"] == "item" else None
            if kind == "size":
                if target is not None and "sizes" in M.MENU_BY_SKU[target["sku"]] and not target.get("_sized"):
                    target["size"], target["_sized"] = val, True
                else:
                    pre["size"] = val
            elif target is not None and val in M.MENU_BY_SKU[target["sku"]]["mods"]:
                target["mods"].append(val)
            else:
                pre["mods"].append(val)
        elif kind in ("item", "retail"):
            it = {"kind": kind, "sku": val, "qty": pre["qty"] or 1, "size": None, "mods": []}
            if kind == "item":
                allowed = M.MENU_BY_SKU[val]["mods"]
                it["mods"] = [x for x in pre["mods"] if x in allowed]
                orphans = [x for x in pre["mods"] if x not in allowed]
                if "sizes" in M.MENU_BY_SKU[val]:
                    it["size"], it["_sized"] = pre["size"] or M.DEFAULT_SIZE, bool(pre["size"])
                # 'a latte and oat milk... a muffin': a modifier the next item can't take belongs to the one before
                if orphans and items and items[-1]["kind"] == "item":
                    items[-1]["mods"] += [x for x in orphans if x in M.MENU_BY_SKU[items[-1]["sku"]]["mods"]]
            items.append(it)
            pre = {"qty": None, "size": None, "mods": []}
            after_sep = False
    loose = {"size": pre["size"], "mods": pre["mods"]}
    if items and items[-1]["kind"] == "item" and (loose["size"] or loose["mods"]):
        last = items[-1]
        last["mods"] += [x for x in loose["mods"] if x in M.MENU_BY_SKU[last["sku"]]["mods"]]
        if loose["size"] and "sizes" in M.MENU_BY_SKU[last["sku"]] and not last.get("_sized"):
            last["size"] = loose["size"]
        loose = {"size": None, "mods": []}
    for it in items:
        it.pop("_sized", None)
        it["mods"] = sorted(set(it["mods"]))
    return items, loose


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------
async def _save(sess: dict):
    sess["updated_at"] = now_iso()
    _SESSIONS[sess["session_id"]] = sess
    await store.upsert("lookups", f"voice::{sess['session_id']}", sess, ttl=SESSION_TTL_SECONDS)


async def load(session_id: str) -> dict:
    sess = _SESSIONS.get(session_id) or await store.get("lookups", f"voice::{session_id}")
    if not sess:
        raise ValueError("That lane session has ended")
    return sess


def _say(sess: dict, role: str, text: str, **extra):
    sess["transcript"].append({"role": role, "text": text, "at": now_iso(), **extra})


def _daypart(hour: int) -> str:
    return "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")


def _key(l: dict) -> str:
    return M.line_key(l["sku"], l.get("size"), l.get("mods") or []) if M.is_menu(l["sku"]) else l["sku"]


def _name(l: dict) -> str:
    return M.describe(l["sku"], l.get("size"), l.get("mods")) if M.is_menu(l["sku"]) else A.short(C.PRODUCT_BY_SKU[l["sku"]]["name"])


def _count(l: dict) -> str:
    name = _name(l)
    if l["qty"] > 1 and M.is_menu(l["sku"]) and not M.MENU_BY_SKU[l["sku"]]["name"].endswith("s"):
        name = name.replace(M.MENU_BY_SKU[l["sku"]]["name"], M.MENU_BY_SKU[l["sku"]]["name"] + "s", 1)
    return f"{l['qty']} {name}"


def _add(sess: dict, line: dict, source: str) -> dict:
    line = {"sku": line["sku"], "qty": int(line.get("qty") or 1), "size": line.get("size"), "mods": list(line.get("mods") or [])}
    if M.is_menu(line["sku"]):
        line = M.normalize_line(line)
    key = _key(line)
    for l in sess["cart"]:
        if _key(l) == key:
            l["qty"] += line["qty"]
            return l
    line["source"] = source
    sess["cart"].append(line)
    return line


async def _shopper(sess: dict) -> dict | None:
    return await store.get("shoppers", f"shopper::{sess['shopper_id']}") if sess.get("shopper_id") else None


async def _identify(sess: dict, shopper_id: str = "", member_number: str = "", method: str = "") -> dict | None:
    who = await pos.identify_guest(shopper_id=shopper_id, member_number=member_number, store_id=sess["store_id"])
    if not who.get("identified"):
        return None
    sess.update(shopper_id=who["shopper_id"], identified_by=method or "member number",
                guest={k: who.get(k) for k in ("first_name", "last_name", "tier_name", "wallet", "segment", "usual_text",
                                               "usual_total_usd", "offers", "member_number")})
    sess["guest"]["usual"] = who["usual"]
    metrics.bump("voice_guests_recognized")
    return who


async def start(store_id: str, channel: str = "drive_thru", lane: str = "", shopper_id: str = "", member_number: str = "",
                identified_by: str = "") -> dict:
    if channel not in ("drive_thru", "phone"):
        raise ValueError("channel must be drive_thru or phone")
    if store_id not in C.STORES:
        raise ValueError("Unknown store")
    st = C.STORES[store_id]
    sess = {"type": "voice_session", "session_id": uuid.uuid4().hex[:12], "store_id": store_id, "store_name": st["name"],
            "channel": channel, "lane": lane or ("Lane 1" if channel == "drive_thru" else "Phone line"), "state": "ordering",
            "shopper_id": "", "identified_by": None, "guest": None, "cart": [], "pending": None, "offered": [], "transcript": [],
            "turns": 0, "ticket_id": None, "started_at": now_iso()}
    metrics.bump("voice_sessions")
    who = await _identify(sess, shopper_id, member_number, identified_by) if (shopper_id or member_number) else None
    ctx = await pos.context(store_id, await _shopper(sess))
    if who:
        sess["pending"] = {"kind": "usual"}
        hello = (f"{_daypart(ctx['hour'])}, {who['first_name']}, welcome back to ShopSmart Café."
                 if channel == "drive_thru" else f"Hi {who['first_name']}, thanks for calling {st['name']}.")
        greeting = f"{hello} Would you like your usual, {who['usual_text']}?"
    elif channel == "drive_thru":
        greeting = ("Welcome to ShopSmart Café. Order whenever you're ready. If you're a Rewards member, "
                    "tell me your member number and I'll pull up your price.")
    else:
        greeting = (f"Thanks for calling {st['name']}. What can I get started for pickup? If you're a Rewards member, "
                    "give me your member number any time.")
    _say(sess, "agent", greeting)
    await _save(sess)
    return await view(sess, reply=greeting, ctx=ctx, trace={"intent": "greeting", "engine": "store edge", "ms": 0.0})


async def _llm_parse(sess: dict, text: str) -> tuple[list[dict], dict | None]:
    """Fallback for an utterance the grammar can't read: ask the LLM, through
    AOM's gateway, to restate it in menu words - then parse that."""
    state = await pos.get_state(sess["store_id"])
    if not aom.enabled or state["wan"] != "up":
        return [], None
    names = ", ".join(m["name"] for m in M.MENU)
    prompt = ("Restate a drive-thru guest's order using only these menu names: " + names + ". Sizes: small, medium, large. "
              "Reply with the restated order on one line and nothing else, or NONE if they are not ordering.\nGuest: " + text)
    token = current_agent.set("voice")
    t0 = time.perf_counter()
    try:
        res = await aom.complete(prompt, f"rag:{config.RAG_APP_ID}:voice")
    except AOMError as exc:
        log.info("voice LLM fallback unavailable: %s", exc)
        return [], None
    finally:
        current_agent.reset(token)
    info = {"status": (res.get("cache") or {}).get("status") or res.get("cache_status") or "miss", "stub": bool(res.get("stub")),
            "ms": round((time.perf_counter() - t0) * 1000, 1)}
    if res.get("stub"):
        return [], info
    items, _ = parse_items(normalize(str(res.get("response") or "")))
    return items, info


async def turn(session_id: str, text: str) -> dict:
    sess = await load(session_id)
    t0 = time.perf_counter()
    raw = (text or "").strip()[:400]
    t = normalize(raw)
    _say(sess, "guest", raw)
    sess["turns"] += 1
    say: list[str] = []
    trace: dict = {"intent": [], "entities": [], "engine": "edge grammar"}
    added = False
    wants_done = False
    llm = None

    if sess["state"] == "placed":
        say.append("Your order is in. Pull forward whenever you're ready." if sess["channel"] == "drive_thru"
                   else "Your order is in. See you at the pickup counter.")
        return await _finish(sess, say, trace, t0)

    # -- who is this? a spoken member number re-prices the whole order
    digits = re.sub(r"\b(zero|oh|one|two|three|four|five|six|seven|eight|nine)\b", lambda m: str(NUMBERS[m.group(1)]), t)
    mm = MEMBER_RE.search(digits) or (re.fullmatch(r"((?:\d\s?){6})", digits) if not sess["shopper_id"] else None)
    if mm:
        number = re.sub(r"\s", "", mm.group(1))
        who = await _identify(sess, member_number=number, method="spoken member number")
        trace["intent"].append("identify")
        if who:
            say.append(f"Thanks, {who['first_name']}. I've got your {who['tier_name']} pricing on this order.")
            if not sess["cart"]:
                sess["pending"] = {"kind": "usual"}
                say.append(f"Want your usual, {who['usual_text']}?")
        else:
            say.append("I couldn't find that member number, so I'll keep going with regular pricing.")
        return await _finish(sess, say, trace, t0)

    # -- yes / no to whatever the agent last asked
    pending, sess["pending"] = sess.get("pending"), None
    toks = t.split()
    starts_with_mod = any(k == "mod" and tuple(toks[:len(w)]) == w for w, k, _ in ALIASES)   # "no cheese" is not a "no"
    yes = YES_RE.match(t)
    no = None if (yes or starts_with_mod) else NO_RE.match(t)
    if USUAL_RE.search(t):
        t = USUAL_RE.sub("", t).strip()
        if sess.get("guest"):
            yes, pending = YES_RE.match("yes"), {"kind": "usual"}
            t = "yes " + YES_RE.sub("", t, count=1).strip()
        else:
            say.append("I don't know your usual yet. Tell me your Rewards member number and I'll pull it up.")
    if pending and yes:
        t = t[yes.end():].strip()
        if pending["kind"] == "usual":
            for l in sess["guest"]["usual"]:
                _add(sess, l, "usual")
            say.append("You got it: " + " and ".join(_count(l) for l in sess["guest"]["usual"]) + ".")
            trace["intent"].append("accept_usual")
            added = True
        elif pending["kind"] == "offer":
            s = pending["offer"]
            if s["kind"] == "upsize":
                for l in sess["cart"]:
                    if _key(l) == s["key"]:
                        l["size"], l["upsold"] = "large", True
            else:
                _add(sess, s["line"], s["source"])
            metrics.bump("voice_offers_accepted")
            metrics.bump("voice_offer_revenue_usd", float(s.get("net_usd") or 0))
            sess.setdefault("accepted", []).append(s["id"])
            say.append("Added." if s["kind"] != "upsize" else "Large it is.")
            trace["intent"].append(f"accept_offer:{s['kind']}")
            added = True
        elif pending["kind"] == "confirm":
            return await _place(sess, say, trace, t0)
    elif pending and no:
        t = t[no.end():].strip()
        trace["intent"].append(f"decline:{pending['kind']}")
        if pending["kind"] == "confirm":
            sess["state"] = "ordering"
            say.append("No problem. What should I change?")
            if not t:
                return await _finish(sess, say, trace, t0)
        elif not t or DONE_RE.search(t):
            say.append("No problem.")
    elif pending and pending["kind"] == "confirm":
        sess["state"] = "ordering"      # they kept ordering instead of confirming
    elif not pending and (yes or no) and not t[(yes or no).end():].strip():
        if no and sess["cart"]:
            wants_done = True
        else:
            say.append("What can I get for you?")
            return await _finish(sess, say, trace, t0)
        t = ""

    # -- the rest of the utterance
    if t:
        if CLEAR_RE.search(t):
            sess["cart"], sess["offered"] = [], []
            say.append("Okay, I've cleared the order. What would you like?")
            trace["intent"].append("clear")
            return await _finish(sess, say, trace, t0)
        items, loose = parse_items(t)
        if not items and not loose["mods"] and not loose["size"] and not any(
                r.search(t) for r in (DONE_RE, TOTAL_RE, DEALS_RE, READBACK_RE, REMOVE_RE)):
            items, llm = await _llm_parse(sess, raw)
            if items:
                trace["engine"] = "AOM LLM gateway"
        trace["entities"] = [{"item": _name(i), "qty": i["qty"]} for i in items]
        if REMOVE_RE.search(t) and items:
            gone = []
            for it in items:
                hit = next((l for l in reversed(sess["cart"]) if l["sku"] == it["sku"]), None)
                if hit:
                    sess["cart"].remove(hit)
                    gone.append(_name(hit))
            say.append(("I took off the " + " and the ".join(gone) + ".") if gone else "That wasn't on the order.")
            trace["intent"].append("remove")
        elif items:
            ctx0 = await pos.context(sess["store_id"], None)
            heard = []
            for it in items:
                if ctx0["kitchen"].get(it["sku"], {}).get("out"):
                    say.append(f"Sorry, we just sold out of {M.MENU_BY_SKU[it['sku']]['name'].lower()}s.")
                    continue
                _add(sess, it, "voice")
                heard.append(_count(M.normalize_line(it) if M.is_menu(it["sku"]) else it))
            if heard:
                say.append("Got it: " + ", ".join(heard) + ".")
                added = True
            trace["intent"].append("add")
        elif (loose["mods"] or loose["size"]) and sess["cart"]:
            # 'make that a small', 'no cheese on that': the most recent line that can take the change
            def fits(l):
                m = M.MENU_BY_SKU.get(l["sku"])
                return bool(m) and (("sizes" in m) if loose["size"] else any(x in m["mods"] for x in loose["mods"]))
            last = next((l for l in reversed(sess["cart"]) if fits(l)), None)
            if last:
                m = M.MENU_BY_SKU[last["sku"]]
                if loose["size"] and "sizes" in m:
                    last["size"] = loose["size"]
                last["mods"] = sorted(set(last["mods"]) | {x for x in loose["mods"] if x in m["mods"]})
                say.append(f"Okay, {_count(last)}.")
                trace["intent"].append("modify")
        if DONE_RE.search(t):
            wants_done = True
        elif TOTAL_RE.search(t) and not items:
            priced, tot, _ = await _priced(sess)
            say.append(f"You're at {money(tot['total_usd'])} with tax so far." if priced else "Nothing on the order yet.")
            trace["intent"].append("total")
        elif DEALS_RE.search(t) and not items:
            say.append(await _deals(sess))
            trace["intent"].append("deals")
        elif READBACK_RE.search(t):
            priced, tot, _ = await _priced(sess)
            say.append(("So far: " + ", ".join(_count(l) for l in sess["cart"]) + f". {money(tot['total_usd'])} with tax.") if priced
                       else "Nothing on the order yet.")
            trace["intent"].append("readback")
        elif not trace["intent"]:
            trace["intent"].append("unknown")
            say.append("Sorry, I didn't catch that. You can order by name, like a large latte with oat milk, "
                       "or say that's all when you're done.")
            return await _finish(sess, say, trace, t0, llm)

    if wants_done:
        if not sess["cart"]:
            say.append("I don't have anything on the order yet. What can I get you?")
            return await _finish(sess, say, trace, t0, llm)
        priced, tot, _ = await _priced(sess)
        why = [p["label"] for p in tot["promotions"]]
        tier = (sess.get("guest") or {}).get("tier_name")
        saved = ""
        if tot["savings_usd"]:
            reasons = ([f"{tier} pricing"] if tier and tier != "Member" else []) + [f"the {w.lower()}" for w in why]
            how = (" with " + (", ".join(reasons[:-1]) + " and " + reasons[-1] if len(reasons) > 1 else reasons[0])) if reasons else ""
            saved = f" You're saving {money(tot['savings_usd'])}{how}."
        say.append("I have " + ", ".join(_count(l) for l in sess["cart"]) + f". Your total is {money(tot['total_usd'])}.{saved} Is that right?")
        sess["pending"] = {"kind": "confirm"}
        sess["state"] = "confirming"
        trace["intent"].append("done")
        return await _finish(sess, say, trace, t0, llm)

    if added:
        offer = await _next_offer(sess)
        if offer:
            sess["pending"] = {"kind": "offer", "offer": offer}
            sess["offered"].append(offer["id"])
            metrics.bump("voice_offers_made")
            say.append(offer["pitch"])
            trace["offer"] = {"kind": offer["kind"], "label": offer["label"], "net_usd": offer.get("net_usd")}
        else:
            say.append("Anything else?")
    elif not say or not say[-1].rstrip().endswith("?"):
        say.append("Anything else?")
    return await _finish(sess, say, trace, t0, llm)


async def _priced(sess: dict, ctx: dict | None = None) -> tuple[list[dict], dict, dict]:
    shopper = await _shopper(sess)
    ctx = ctx or await pos.context(sess["store_id"], shopper)
    if not sess["cart"]:
        return [], pos.totals(shopper, []), ctx
    priced = pos.price(ctx, sess["cart"])
    return priced, pos.totals(shopper, priced), ctx


async def _next_offer(sess: dict) -> dict | None:
    if len(sess["offered"]) >= MAX_OFFERS:
        return None
    ctx = await pos.context(sess["store_id"], await _shopper(sess))
    kinds = {o.split(":")[0] for o in sess["offered"]}       # one offer of each kind per order
    options = [s for s in await pos.suggestions(ctx, sess["cart"], sess["channel"])
               if s["id"].split(":")[0] not in kinds and s["kind"] in OFFER_ORDER]
    options.sort(key=lambda s: OFFER_ORDER[s["kind"]])
    return options[0] if options else None


async def _deals(sess: dict) -> str:
    ctx = await pos.context(sess["store_id"], await _shopper(sess))
    bits = []
    for w in ctx["pricing"].get("time_rules", []):
        if P.window_active(w, ctx["hour"]) and (w.get("tags") or w.get("requires_option") == C.PICKUP_OPTION):
            bits.append(f"{w['label']}: {w['pct_off']}% off " + ("coffee and breakfast" if "coffee" in (w.get("tags") or []) else "everything at the store"))
    for sku, k in ctx["kitchen"].items():
        if k.get("overstock"):
            bits.append(f"fresh {M.MENU_BY_SKU[sku]['name'].lower()}s are {k['overstock']}% off")
    bits += [f"the {c['name'].lower()} takes {c['pct_off']}% off {c['reason'].lower()}" for c in M.COMBOS]
    for o in ctx["offers"][:1]:
        bits.append(f"and you have a personal offer: {o['title']}")
    return "Right now: " + "; ".join(bits) + "."


async def _place(sess: dict, say: list[str], trace: dict, t0: float) -> dict:
    shopper = await _shopper(sess)
    priced, tot, ctx = await _priced(sess)
    sold_out = [l for l in priced if l.get("sold_out")]
    if sold_out:
        sess["cart"] = [l for l in sess["cart"] if l["sku"] not in {x["sku"] for x in sold_out}]
        sess["state"] = "ordering"
        say.append(f"Sorry, the kitchen just ran out of {M.MENU_BY_SKU[sold_out[0]['sku']]['name'].lower()}s, so I took that off. Anything else?")
        return await _finish(sess, say, trace, t0)
    lines = [{"sku": l["sku"], "qty": l["qty"], "size": l.get("size"), "mods": l.get("mods") or []} for l in sess["cart"]]
    sources = {_key(l): ("upsell" if l.get("upsold") else l.get("source") or "voice") for l in sess["cart"]}
    payment = ((shopper or {}).get("wallet") or {}).get("payment_method") or ("Pay at the window" if sess["channel"] == "drive_thru" else "Pay at pickup")
    args = {"lines": lines, "sources": sources, "payment": payment, "lane": sess["lane"], "session_id": sess["session_id"],
            "pricing_version": ctx["pricing"]["version"], "priced_hour": ctx["hour"]}
    who = f"{sess['guest']['first_name']} · " if sess.get("guest") else ""
    title = f"{pos.CHANNELS[sess['channel']]} · {who}{len(lines)} item{'s' if len(lines) > 1 else ''} · {money(tot['total_usd'])}"
    key, doc = await pos.create_ticket(sess["store_id"], sess["channel"], sess["shopper_id"], args, title, "voice", priced, tot)
    # What the guest was quoted stays on the screen: the order itself changes their wallet a moment later.
    sess.update(state="placed", ticket_id=key, placed={"cart": priced, "totals": tot})
    name = f", {sess['guest']['first_name']}" if sess.get("guest") else ""
    pay = f" on your {payment}" if shopper else ""
    credit = f" after {money(tot['store_credit_applied_usd'])} store credit" if tot["store_credit_applied_usd"] else ""
    if sess["channel"] == "drive_thru":
        say.append(f"Thanks{name}! That's {money(tot['amount_due_usd'])}{pay}{credit}. Pull forward to the window.")
    else:
        prep = sum(M.MENU_BY_SKU[l["sku"]]["prep_seconds"] for l in priced if l["made_to_order"])
        say.append(f"Thanks{name}! That's {money(tot['amount_due_usd'])}{pay}{credit}. It'll be ready at {sess['store_name']} "
                   f"in about {max(5, round(prep / 90))} minutes.")
    trace["intent"].append("place_order")
    trace["ticket"] = {"id": key, "status": doc["status"]}
    return await _finish(sess, say, trace, t0)


async def _finish(sess: dict, say: list[str], trace: dict, t0: float, llm: dict | None = None) -> dict:
    reply = " ".join(x for x in say if x)
    state = await pos.get_state(sess["store_id"])
    ms = round((time.perf_counter() - t0) * 1000, 2)
    trace.update(ms=ms, wan=state["wan"], llm=llm, intent=" + ".join(trace["intent"]) or "chat")
    _say(sess, "agent", reply, trace=trace)
    metrics.voice_turn(ms, state["wan"] != "up", bool(llm and not llm.get("stub")))
    await _save(sess)
    return await view(sess, reply=reply, trace=trace)


async def view(sess: dict, reply: str | None = None, ctx: dict | None = None, trace: dict | None = None) -> dict:
    priced, tot, ctx = await _priced(sess, ctx)
    if sess.get("placed"):
        priced, tot = sess["placed"]["cart"], sess["placed"]["totals"]
    ticket = None
    if sess.get("ticket_id"):
        d = await store.get("orders", sess["ticket_id"]) or {}
        item = d.get("item") or {}
        ticket = {"id": sess["ticket_id"], "status": d.get("status"), "summary": d.get("summary"), "order_id": item.get("order_id"),
                  "governed": d.get("governed"), "trace_id": d.get("trace_id"), "queued_on_edge": d.get("queued_on_edge"),
                  "total_usd": item.get("total_usd") or d.get("edge_total_usd"), "points_earned": item.get("points_earned"),
                  "ready_in_minutes": item.get("ready_in_minutes"), "pickup_code": item.get("pickup_code")}
    made = {o.split(":")[0] for o in sess["offered"]}
    board = [] if sess["state"] == "placed" else [
        {k: s.get(k) for k in ("id", "kind", "label", "pitch", "net_usd")}
        for s in sorted(await pos.suggestions(ctx, sess["cart"], sess["channel"]), key=lambda s: OFFER_ORDER.get(s["kind"], 9))
        if s["id"].split(":")[0] not in made and s["kind"] in OFFER_ORDER][:4]
    return {"session_id": sess["session_id"], "store_id": sess["store_id"], "store_name": sess["store_name"], "channel": sess["channel"],
            "lane": sess["lane"], "state": sess["state"], "guest": sess.get("guest"), "shopper_id": sess.get("shopper_id") or None,
            "identified_by": sess.get("identified_by"), "cart": priced, "totals": tot, "pending": sess.get("pending"),
            "suggestions": board, "transcript": sess["transcript"][-30:], "reply": reply, "trace": trace, "ticket": ticket,
            "wan": ctx["state"]["wan"], "hour": ctx["hour"], "pricing_version": ctx["pricing"]["version"],
            "reads": ["catalog::v1 + café menu", f"pricing::{ctx['fc']} (v {ctx['pricing']['version'][:8]})",
                      f"posstate::{sess['store_id']}"] + ([f"shopper::{sess['shopper_id']}", f"recs::{sess['shopper_id']}"]
                                                          if sess.get("shopper_id") else [])}
