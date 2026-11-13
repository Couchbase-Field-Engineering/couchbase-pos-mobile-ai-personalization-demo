"""ShopSmart POS Assistant - the agent behind the app's Assistant tab.

One turn:

1. **Classify** the question into an intent, and decide whether it is about
   *this shopper* (needs their data: "what am I running low on?") or a
   *Shopping Guide* question anyone could ask ("how does a price match
   work?").
2. **Guide questions -> AOM RAG Application.** One call to
   `/v1/agent/rag/shopsmart-pos-assistant/query`. The app is scoped to
   today's Shopping Guide document; AOM runs retrieval behind its Context
   Cache and generation behind its LLM Cache, and counts the query on its RAG
   Applications page. If the app isn't registered (or AOM predates RAG
   Applications) the question takes the shopper path below without shopper
   data.
3. **Shopper questions -> Context Caching.** Look the context up in AOM's
   Context Cache (`/v1/context/get`, namespace
   `rag:shopsmart-pos-assistant:shopper-context`), then in the Couchbase
   `ctx::<shopper>::<intent>` document. On a miss, assemble it: AOM Knowledge
   Base retrieval scoped to this shopper's profile document and to the
   guide, plus live facts from the governed MCP tools (smart cart, member
   prices, fulfillment-center stock, wallet). It is stored in AOM
   (`/v1/context/set`, with the real build time) and in Couchbase, keyed by
   the export date and the shopper revision, so the next daily load or any
   order invalidates it. The Couchbase copy carries the shopper's channel, so
   Sync Gateway puts it on their phone too - which is what lets the app
   answer offline.
4. **Memory** - recall what this shopper has told the assistant before (AOM
   agent memory), live every turn.
5. **LLM Caching** - complete through AOM `/v1/llm/complete` in the
   `rag:shopsmart-pos-assistant:shopper` namespace (the `rag:<app>:`
   prefix is how AOM attributes the activity to the RAG application).
6. Suggest one-tap actions; the app turns a tap into an offline-first
   `orders` document.
"""
from __future__ import annotations

import logging
import re
import time
import uuid

from . import catalog as C
from . import config, knowledge, metrics, rag_app, tool_gateway
from .aom_client import AOMError, aom, current_trace
from .docs import STATE, chat_id, ctx_id, days_since, money, now_iso, shopper_channel, shopper_id_doc
from .store import store

log = logging.getLogger("pos.assistant")

INTENTS = ["reorder", "pricing", "availability", "delivery", "returns", "rewards", "history", "subscription", "preferences", "deals",
           "general"]
# Context-cache keys: shopper context per intent, plus a guide-only context
# per intent for generic questions (no shopper data in it).
CONTEXT_KEYS = INTENTS + [f"guide_{i}" for i in INTENTS]

KEYWORDS = {
    "deals": ["deals", "deal", "offer", "offers", "promo", "promotion", "on sale", "sale today", "bundle", "bundles", "coupon",
              "markdown", "marked down", "goes with", "go with", "pair with"],
    "rewards": ["points", "store credit", "credit", "wallet", "rewards", "membership", "member", "tier", "plus", "premier", "balance",
                "upgrade"],
    "returns": ["return", "refund", "damaged", "wrong item", "broken", "send back", "goodwill", "exchange", "arrived"],
    "subscription": ["subscribe", "subscription", "recurring", "auto-ship", "autoship", "every week", "every month", "schedule",
                     "cadence"],
    "availability": ["in stock", "available", "availability", "on hand", "backorder", "out of", "sold out", "aisle",
                     "limit", "limited", "more than one", "second", "where can i find", "which aisle"],
    "delivery": ["deliver", "delivery", "shipping", "shipped", "ships", "arrive", " eta", "when will", "pickup", "pick up",
                 "same-day", "delivery fee"],
    "pricing": ["price", "pricing", "cost", "how much", "discount", "cheaper", "savings", "price match", "match", "competitor",
                "multi-buy"],
    "history": ["history", "last order", "last time", "previous", "ordered before", "usually", "how often", "spend", "spent",
                "what did i", "my order", "track", "order status", "where is my"],
    "preferences": ["preference", "prefer", "favorite", "hide", "stop showing", "don't show", "not interested", "more of",
                    "less of", "substitut"],
    "reorder": ["reorder", "re-order", "running low", "low on", "run out", "restock", "smart cart", "buy again", "recommend",
                "what should", "suggest", "for me", "need", "cart", "coffee", "diapers", "paper towels", "detergent", "dog food"],
}
PROBLEM_WORDS = ("damaged", "broken", "wrong item", "crushed", "missing item", "defective")
PERSONAL_RE = re.compile(r"\b(i|i'm|i've|i'd|im|me|my|mine|we|we're|our|us)\b", re.I)
PERSONAL_INTENTS = {"reorder", "history", "deals"}
# Words that name something in the catalog: a question that mentions one needs
# this shopper's member price and their fulfillment center's stock.
PRODUCT_WORDS = {w for p in C.PRODUCTS for w in re.findall(r"[a-z]{3,}", (p["name"] + " " + " ".join(p["tags"])).lower())} - {
    "adult", "balance", "building", "bundle", "clear", "count", "cover", "creative", "daily", "extra", "family", "field",
    "first", "free", "gal", "gift", "home", "indoor", "kids", "large", "little", "loads", "medium", "mega", "mix", "morning",
    "night", "pack", "packs", "person", "phone", "piece", "pieces", "safety", "select", "sensitive", "serve", "set", "single",
    "size", "smart", "stand", "steps", "summer", "sun", "supplies", "table", "tall", "travel", "variety", "with", "kit",
    "sea", "car", "art", "air", "peak", "trail", "spring", "golden", "harvest", "wellness", "commute", "streaming", "dinner"}

# Canonical retrieval query per intent: the cached context is per intent, so
# it is retrieved with a stable query rather than the wording of whichever
# question happened to miss the cache first.
INTENT_QUERY = {
    "reorder": "smart cart replenishment days to runout order history recommendations",
    "pricing": "member pricing tier discount multi-buy price match competitor",
    "availability": "stock on hand backorder fulfillment center purchase limit aisle",
    "delivery": "delivery options same-day pickup free delivery fee",
    "returns": "returns policy refund store credit damaged goodwill credit",
    "rewards": "rewards points store credit membership tier renewal",
    "history": "order history spend last order replenishment",
    "subscription": "subscribe and save recurring cadence schedule",
    "preferences": "preferences favorite categories hidden categories substitutions recommendations",
    "deals": "personal offers promotions bundles overstock markdown price drop loyalty",
    "general": "frequently asked questions policy",
}

PERSONA = ("You are the ShopSmart POS Assistant in the Couchbase ShopSmart mobile app, talking to the shopper. Be "
           "friendly, direct and concise (under 110 words) and specific: item names, quantities, prices, dates. Use only "
           "the facts in CONTEXT. Never invent prices, stock or orders. Never say an order is placed - the shopper "
           "confirms actions with the buttons shown under your reply.")


def mentions_product(m: str) -> bool:
    return any(w in PRODUCT_WORDS for w in re.findall(r"[a-z]{3,}", m))


def classify(message: str) -> tuple[str, bool]:
    m = message.lower()
    scores = {i: sum(1 for k in kws if k in m) for i, kws in KEYWORDS.items()}
    intent = max(scores, key=scores.get)
    if scores[intent] == 0:
        intent = "general"
    # Something went wrong with an item: that is a return or a credit, whatever else the sentence mentions.
    if any(k in m for k in PROBLEM_WORDS):
        return "returns", True
    personal = bool(PERSONAL_RE.search(message)) or intent in PERSONAL_INTENTS
    if intent in ("pricing", "availability") and mentions_product(m):
        return intent, True
    if intent == "general":
        if mentions_product(m):
            return "availability", True
        return "general", False
    # A policy question phrased generically ("how does a price match work?")
    # stays on the shared guide path even though its intent is shopper-capable.
    if not PERSONAL_RE.search(message) and re.match(r"\s*(how|what|is|are|can|do|does|who|which|why)\b", m) \
            and intent not in PERSONAL_INTENTS:
        return intent, False
    return intent, personal


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", q.lower()).strip()


async def kb_doc_id(key: str) -> str | None:
    doc = await store.get("lookups", f"kbdoc::{key}")
    return doc.get("document_id") if doc else None


# --------------------------------------------------------------------------
# Context cache
# --------------------------------------------------------------------------
async def invalidate_context(shopper_id: str):
    for key in INTENTS:
        await store.remove("context_cache", ctx_id(shopper_id, key))


async def _kb(query: str, document_id: str | None, top_k=3) -> tuple[list[dict], str]:
    if not (aom.enabled and document_id):
        return [], "unavailable"
    try:
        res = await aom.knowledge_search(query, top_k=top_k, document_id=document_id)
        return [{"title": r.get("document_title"), "content": r.get("content"), "score": round(r.get("score") or 0, 3),
                 "document_id": r.get("document_id"), "source": "aom-kb"} for r in res.get("results", [])], "aom"
    except AOMError as exc:
        log.warning("KB search failed: %s", exc)
        return [], "error"


def _local_profile_chunks(shopper: dict, intent: str) -> list[dict]:
    md = knowledge.shopper_profile_markdown(shopper)
    sections = md.split("\n## ")
    want = {"reorder": ["Recommendations", "Orders"], "pricing": ["Membership", "Recommendations"], "availability": ["Shopper", "Recommendations"],
            "delivery": ["Shopper"], "returns": ["Orders", "Membership"], "rewards": ["Membership"],
            "history": ["Orders"], "subscription": ["Orders", "Recommendations"], "preferences": ["Preferences", "Recommendations"],
            "deals": ["Membership", "Recommendations"], "general": ["Shopper"]}[intent]
    out = []
    for s in sections:
        if any(s.startswith(w) for w in want):
            out.append({"title": f"Shopper profile {shopper['shopper_id']}", "content": s[:1200], "score": None,
                        "document_id": None, "source": "local-dump"})
    return out


def _local_guide_chunks(query: str) -> list[dict]:
    q = query.lower()
    scored = []
    for f in knowledge.FAQ:
        s = sum(1 for k in f["keywords"] if k in q)
        if s:
            scored.append((s, f))
    scored.sort(key=lambda x: -x[0])
    return [{"title": "ShopSmart Shopping Guide", "content": f"{f['q']} {f['a']}", "answer": f["a"], "score": None,
             "document_id": None, "source": "local-guide"} for _, f in scored[:3]]


STOP_WORDS = {"much", "does", "have", "this", "what", "price", "stock", "many", "your", "with", "that", "there", "still", "more",
              "than", "when", "where", "will", "cost", "costs", "about", "from", "they", "want", "need", "another", "second"}


async def _catalog_lookup(shopper_id: str, message: str) -> list[dict]:
    """The items a question mentions, at this shopper's price and stock.
    Looked up live on every turn: the cached context is per intent, not per
    product, so it must not carry the first question's items."""
    hits = [w for w in re.findall(r"[a-z]{3,}", message.lower()) if w in PRODUCT_WORDS and w not in STOP_WORDS]
    if not hits:
        return []
    srch = await tool_gateway.invoke("search_catalog", {"shopper_id": shopper_id, "query": " ".join(hits[:4])})
    return (srch.get("data", srch) or {}).get("items", [])[:5]


async def _facts(shopper_id: str, intent: str) -> dict:
    if intent in ("reorder", "pricing", "availability", "subscription", "preferences", "deals"):
        tool = "get_recommendations"
    else:
        tool = "get_shopper_context"
    res = await tool_gateway.invoke(tool, {"shopper_id": shopper_id})
    data = res.get("data", res)
    facts = {"tool": tool, "governed": res.get("governed", False), "trace_id": res.get("trace_id"), "data": data}
    return facts


def _aom_context_key(shopper_id: str, cache_key: str, shopper: dict | None, personal: bool) -> str:
    """Key in AOM's Context Cache. The export date and (for shopper context)
    the shopper revision are part of the key, so a new daily export or any
    order makes the old entry unreachable at once. Guide context holds no
    shopper data, so every shopper shares one entry per intent."""
    if personal and shopper:
        return f"{shopper_id}:{cache_key}:{shopper.get('export_date')}:r{shopper.get('rev', 0)}"
    return f"{cache_key}:{STATE.get('export_date')}"


def _local_valid(doc: dict | None, shopper: dict | None, personal: bool) -> bool:
    if not doc or doc.get("export_date") != STATE.get("export_date"):
        return False
    return not personal or (shopper is not None and doc.get("shopper_rev") == shopper.get("rev", 0))


async def get_context(shopper: dict, intent: str, personal: bool, message: str = "") -> dict:
    """Context Caching, with AOM's Context Cache in front of the assembly.

    1. AOM `/v1/context/get` in the `rag:<app>:shopper-context` namespace, so
       hit rate and latency avoided show on AOM's Context Cache and RAG
       Applications pages.
    2. On a miss (or with AOM unreachable), the Couchbase `context_cache`
       document, if it was built from today's export and the current
       shopper revision.
    3. Otherwise assemble: KB retrieval scoped to this shopper's profile
       document and the guide, plus live facts from the governed MCP tools.
       The result goes to AOM (`/v1/context/set` with the real build time as
       `source_latency_ms`) and to Couchbase, where the shopper's channel
       puts it on their phone for offline answers.
    """
    shopper_id = shopper["shopper_id"]
    cache_key = intent if personal else f"guide_{intent}"
    key = ctx_id(shopper_id, cache_key)
    channels = [shopper_channel(shopper_id)]
    aom_key = _aom_context_key(shopper_id, cache_key, shopper, personal)
    t0 = time.perf_counter()
    aom_status = "off"
    if aom.enabled:
        try:
            res = await aom.context_get(aom_key, config.CONTEXT_NAMESPACE)
            aom_status = (res.get("cache") or {}).get("status") or ("hit" if res.get("hit") else "miss")
            value = res.get("value")
            if res.get("hit") and isinstance(value, dict):
                # A shared guide entry may have been stored by another shopper's
                # turn: re-home it before it syncs to this shopper's phone.
                doc = dict(value, scope=shopper_id, channels=channels)
                local = await store.get("context_cache", key)
                if not _local_valid(local, shopper, personal):
                    await store.upsert("context_cache", key, doc, ttl=config.CONTEXT_CACHE_TTL_SECONDS)
                doc["_cache"] = {"status": "hit", "source": "aom", "namespace": config.CONTEXT_NAMESPACE,
                                 "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
                return doc
        except AOMError as exc:
            aom_status = "error"
            log.info("AOM context get failed: %s", exc)

    cached = await store.get("context_cache", key)
    if _local_valid(cached, shopper, personal):
        cached["_cache"] = {"status": "hit", "source": "couchbase", "aom": aom_status,
                            "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
        if aom_status == "miss":
            await _aom_context_set(aom_key, cached, cached.get("build_ms"))
        return cached

    query = INTENT_QUERY[intent]
    profile_doc = await kb_doc_id(shopper_id) if personal else None
    guide_doc = await kb_doc_id("guide")
    kb_profile, kb_mode = ([], "skipped")
    if personal and intent != "general":
        kb_profile, kb_mode = await _kb(query, profile_doc)
        if not kb_profile:
            kb_profile = _local_profile_chunks(shopper, intent)
    kb_guide, gd_mode = await _kb(query, guide_doc)
    if not kb_guide:
        kb_guide = _local_guide_chunks(query + " " + intent)
    if kb_mode == "skipped":
        kb_mode = gd_mode
    facts = await _facts(shopper_id, intent) if personal and intent != "general" else {}
    build_ms = round((time.perf_counter() - t0) * 1000, 1)
    doc = {
        "type": "context_cache", "scope": shopper_id, "shopper_id": shopper_id if personal else None,
        "intent": intent, "personal": personal, "query": query,
        "export_date": STATE.get("export_date"), "shopper_rev": shopper.get("rev", 0) if personal else None,
        "built_at": now_iso(), "build_ms": build_ms, "kb_mode": kb_mode,
        "kb_profile": kb_profile, "kb_guide": kb_guide, "facts": facts,
        "profile_document_id": profile_doc, "guide_document_id": guide_doc,
        "aom_key": aom_key, "channels": channels,
    }
    await store.upsert("context_cache", key, doc, ttl=config.CONTEXT_CACHE_TTL_SECONDS)
    await _aom_context_set(aom_key, doc, build_ms)
    doc = dict(doc)
    doc["_cache"] = {"status": "miss", "source": "built", "aom": aom_status, "latency_ms": build_ms}
    return doc


async def _aom_context_set(aom_key: str, doc: dict, build_ms) -> None:
    if not aom.enabled:
        return
    value = {k: v for k, v in doc.items() if not k.startswith("_")}
    if not value.get("personal"):
        if value.get("kb_profile") or value.get("facts"):
            return  # never share shopper data across shoppers' guide entries
        for k in ("scope", "channels", "profile_document_id", "shopper_id"):
            value.pop(k, None)
    try:
        await aom.context_set(aom_key, value, config.CONTEXT_NAMESPACE, ttl_seconds=config.CONTEXT_CACHE_TTL_SECONDS,
                              source_latency_ms=int(build_ms or 0))
    except AOMError as exc:
        # 409 = context caching switched off in AOM's policy, 413 = over its size limit.
        log.info("AOM context set skipped: %s", exc)


# --------------------------------------------------------------------------
# Answer composition
# --------------------------------------------------------------------------
def _short(name: str) -> str:
    return name.split(",")[0]


def _shopper_summary(s: dict) -> str:
    w = s["wallet"]
    last = s["order_history"][0] if s.get("order_history") else None
    return (f"{s['first_name']} ({s['address']['city']}, {s['address']['state']}), {s['membership']['tier_name']} member at "
            f"{s['membership']['discount_pct']}% off list. Store credit ${float(w.get('store_credit_usd', 0)):,.2f}, "
            f"{int(w.get('points', 0)):,} points. "
            + (f"Last order {last['order_id']} on {last['date']} for ${last['total_usd']:,.2f}. " if last else "No recent orders. ")
            + f"Ships from {C.FULFILLMENT_CENTERS[s['delivery']['fulfillment_center']]['name']}; default "
            f"{s['delivery'].get('default_option')}; home store {(s.get('home_store') or {}).get('name')}.")


def _facts_text(facts: dict) -> str:
    d = facts.get("data") or {}
    out = []
    if facts.get("tool") == "get_recommendations":
        rows = [f"{l['qty']} x {l['name']} ({l['sku']}) at ${l['unit_price']} = ${l['line_total']}, {l['on_hand_at_fc']} on hand"
                + (f", runs out in {l['days_to_runout']}d" if l.get("days_to_runout") is not None else "")
                for l in d.get("lines", [])]
        if rows:
            out.append("Smart cart: " + "; ".join(rows))
        t = d.get("totals") or {}
        if t:
            out.append(f"Smart cart total ${t.get('total_usd')} (saves ${t.get('savings_usd')} vs list, earns {t.get('points_earned')} points)")
        for r in (d.get("for_you") or [])[:4]:
            out.append(f"Pick for you: {r['name']} ({r['sku']}) ${r['unit_price']} - {r['reason']}")
        for o in d.get("offers") or []:
            out.append(f"Personal offer: {o['title']} ({o['reason']}; expires {o.get('expires')})")
        for dl in (d.get("deals") or [])[:8]:
            if dl["kind"] == "bundle":
                out.append(f"Bundle: {dl['label']} - {' + '.join(dl['names'])}, {dl['pct_off']}% off each, saves ${dl.get('saves_usd')}")
            elif dl["kind"] == "time":
                out.append(f"This hour: {dl['label']} - {dl['reason']}")
            else:
                out.append(f"Deal: {dl['name']} ${dl.get('your_price')} (list ${dl.get('list_price')}) - {dl['label']}: {dl['reason']}")
        for pm in d.get("price_matches") or []:
            out.append(f"Price watch: {pm['competitor']} has {pm['name']} for ${pm['competitor_price_usd']} "
                       f"(member price ${pm['member_price_usd']}, a match saves ${pm['saving_usd']})")
    elif d:
        w = d.get("wallet") or {}
        out.append(f"Wallet: ${w.get('store_credit_usd')} store credit, {w.get('points')} points, {w.get('tier_name')} tier")
        if d.get("last_order"):
            lo = d["last_order"]
            out.append(f"Last order {lo['order_id']} {lo['date']} ${lo['total_usd']} ({lo.get('delivery_option')}, "
                       f"{len(lo.get('lines', []))} items" + (", backorders" if lo.get("backordered") else "") + ")")
        if d.get("subscriptions"):
            out.append("Subscribe & Save: " + "; ".join(f"{x['qty']} x {_short(x['name'])} every {x['cadence_weeks']}w" for x in d["subscriptions"]))
        if d.get("preferences"):
            p = d["preferences"]
            out.append(f"Preferences: favorites {p.get('favorite_categories') or []}, hidden {p.get('avoid_categories') or []}")
    for it in facts.get("catalog") or []:
        out.append(f"Catalog: {it['name']} ({it['sku']}) ${it['unit_price']} (list ${it['list_price']}), {it['on_hand_at_fc']} on hand"
                   + (f", price includes {', '.join(it['deals'])}" if it.get("deals") else "")
                   + (f", aisle {it['aisle']} at the home store" if it.get("aisle") else "")
                   + (f", limit {it['limit_per_household']} per household" if it.get("limit_per_household") else ""))
    return "\n".join(out)


def build_prompt(message: str, personal: bool, ctx: dict, shopper: dict | None, memories: list[str]) -> str:
    parts = [PERSONA, "", "CONTEXT:"]
    if personal and shopper:
        parts.append("Shopper: " + _shopper_summary(shopper))
        for c in ctx.get("kb_profile", [])[:3]:
            parts.append("Profile note: " + (c.get("content") or "")[:900])
        ft = _facts_text(ctx.get("facts") or {})
        if ft:
            parts.append(ft)
        for m in memories[:3]:
            parts.append("Earlier the shopper said: " + m[:300])
    for c in ctx.get("kb_guide", [])[:3]:
        parts.append("Shopping Guide: " + (c.get("content") or "")[:900])
    parts += ["", f"SHOPPER QUESTION: {message.strip()}", "", "ANSWER:"]
    return "\n".join(parts)


def _limit_note(shopper: dict, item: dict) -> str:
    limit = item.get("limit_per_household")
    if not limit:
        return ""
    bought = [(o["date"], x["qty"]) for o in shopper.get("order_history", []) for x in o.get("lines", [])
              if x["sku"] == item["sku"] and (days_since(o.get("date")) or 0) <= C.PURCHASE_LIMIT_WINDOW_DAYS]
    note = f" It's limited to {limit} per household every {C.PURCHASE_LIMIT_WINDOW_DAYS} days so more shoppers can get one."
    if bought:
        note += f" Your household bought one on {bought[0][0]}, so another order would be refused until the window passes."
    return note


def compose_fallback(intent: str, personal: bool, message: str, ctx: dict, shopper: dict | None) -> str:
    """Deterministic answer from the same context - used when AOM has no LLM
    provider key (it returns a labelled stub) or AOM is not configured."""
    d = (ctx.get("facts") or {}).get("data") or {}
    if not personal or not shopper or intent == "general":
        pb = _local_guide_chunks(message) or ctx.get("kb_guide")
        if pb:
            return pb[0].get("answer") or pb[0]["content"]
        return ("I can help with your smart cart, member prices, what's in stock, delivery and pickup, returns, store "
                "credit and points, and Subscribe & Save.")
    tier = shopper["membership"]["tier_name"]
    if intent == "deals":
        offers, deals = d.get("offers") or [], d.get("deals") or []
        items = (ctx.get("facts") or {}).get("catalog") or []
        if items and re.search(r"go(es)? (well )?with|pair|bundle", message.lower()):
            # Cross-sell for one item: what sells with it, and the bundle that makes it cheaper.
            it = items[0]
            pairs = [f"{_short(C.PRODUCT_BY_SKU[x]['name'])} ({why[0].lower() + why[1:]})" for x, why in C.PAIRS_WITH.get(it["sku"], [])
                     if x in C.PRODUCT_BY_SKU]
            b = next((x for x in deals if x["kind"] == "bundle" and it["sku"] in x["skus"]), None)
            txt = (f"With {_short(it['name'])}, shoppers add " + " and ".join(pairs[:2]) + "." if pairs
                   else f"Nothing in particular sells with {_short(it['name'])}.")
            if b:
                txt += (f" Put {' + '.join(b['names'])} in the cart together and each is {b['pct_off']}% off "
                        f"(saves {money(b.get('saves_usd', 0))}).")
            return txt
        parts = []
        if offers:
            o = offers[0]
            parts.append(f"Your offer: {o['title']}. {o['reason']}; good until {o.get('expires')}"
                         + (f". You have {len(offers) - 1} more in the app" if len(offers) > 1 else "") + ".")
        now = [x for x in deals if x["kind"] == "time"]
        if now:
            parts.append(f"This hour, {now[0]['label']}: {now[0]['reason']}.")
        marked = [x for x in deals if x.get("applies")]
        if marked:
            parts.append("Price drops at your fulfillment center: " + "; ".join(
                f"{_short(x['name'])} {money(x['your_price'])} ({x['label'][0].lower() + x['label'][1:]})" for x in marked[:3]) + ".")
        bundles = [x for x in deals if x["kind"] == "bundle"]
        if bundles:
            b = bundles[0]
            parts.append(f"Bundle: {' + '.join(b['names'])} together are {b['pct_off']}% off each (saves {money(b.get('saves_usd', 0))})"
                         + (f", and {len(bundles) - 1} more bundles in the app" if len(bundles) > 1 else "") + ".")
        return " ".join(parts) or "No promotions are running for you right now; every price in the app is already your member price."
    if intent == "subscription":
        it = next((i for i in (ctx.get("facts") or {}).get("catalog") or [] if C.PRODUCT_BY_SKU[i["sku"]]["consumable"]), None)
        if it:
            return (f"{_short(it['name'])} can go on Subscribe & Save at your {tier} price of {money(it['unit_price'])}: pick 1, 2, "
                    "4, 6, 8 or 12 weeks, pause any time, and it leaves your smart cart so you never double up.")
    if intent in ("reorder", "subscription", "preferences"):
        lines = d.get("lines", [])
        if intent == "preferences":
            prefs = shopper.get("preferences", {})
            fav = ", ".join(C.CATEGORIES[c]["name"] for c in prefs.get("favorite_categories", []) if c in C.CATEGORIES) or "nothing yet"
            hide = ", ".join(C.CATEGORIES[c]["name"] for c in prefs.get("avoid_categories", []) if c in C.CATEGORIES) or "nothing"
            return (f"Right now I'm showing you more of {fav} and hiding {hide}. Change that below and your picks are "
                    "re-ranked on the next sync - no waiting for tomorrow's model run.")
        if not lines:
            return "You're well stocked according to this morning's forecast - nothing is due to run out yet."
        urgent = [l for l in lines if l.get("days_to_runout") is not None and l["days_to_runout"] <= 7]
        short = [l for l in lines if l["on_hand_at_fc"] < l["qty"]]
        t = d.get("totals") or {}
        txt = (f"Your smart cart has {len(lines)} item{'s' if len(lines) > 1 else ''}, {money(t.get('total_usd', 0))} at your "
               f"{tier} price (saving {money(t.get('savings_usd', 0))} vs list).")
        if urgent:
            txt += " Most urgent: " + ", ".join(
                f"{_short(l['name'])} ({'likely out already' if l['days_to_runout'] <= 0 else 'about ' + str(l['days_to_runout']) + ' days left'})"
                for l in urgent[:2]) + "."
        if short:
            txt += " Heads up: " + ", ".join(f"only {l['on_hand_at_fc']} {_short(l['name'])} left at your fulfillment center"
                                             for l in short[:2]) + ", so part of it may backorder."
        w = d.get("wallet") or {}
        if w.get("store_credit_usd"):
            txt += f" Your {money(w['store_credit_usd'])} store credit comes off automatically."
        if intent == "subscription":
            top = lines[0]
            txt += (f" {_short(top['name'])} is your most regular buy - Subscribe & Save {top['qty']} every 4 weeks and it "
                    "leaves the smart cart for good.")
        return txt
    if intent in ("pricing", "availability"):
        items = (ctx.get("facts") or {}).get("catalog") or []
        pms = d.get("price_matches") or []
        if items:
            it = items[0]
            fc = C.FULFILLMENT_CENTERS[shopper["delivery"]["fulfillment_center"]]["name"]
            stock = (f"{it['on_hand_at_fc']} on hand at the {fc}" if it["on_hand_at_fc"] > 0
                     else f"out of stock at the {fc} - it would backorder")
            txt = (f"{it['name']} is {money(it['unit_price'])} for you ({tier}, list {money(it['list_price'])}"
                   + (f", including {' and '.join(x.lower() for x in it['deals'][:2])}" if it.get("deals") else "") + f"); {stock}"
                   + (f", and it's in aisle {it['aisle']} at {(shopper.get('home_store') or {}).get('name')}" if it.get("aisle") else "") + ".")
            pm = next((p for p in pms if p["sku"] == it["sku"]), None)
            if pm:
                txt += (f" {pm['competitor']} has it for {money(pm['competitor_price_usd'])} - check out with a price match "
                        f"and save {money(pm['saving_usd'])} once Customer Care approves.")
            elif intent == "pricing":
                txt += " Multi-buy: 3+ takes another 5% off, 6+ another 10%."
            up = C.UPGRADES.get(it["sku"])
            if up and intent == "pricing":
                txt += f" Worth a look: {_short(C.PRODUCT_BY_SKU[up[0]]['name'])} - {up[1][0].lower() + up[1][1:]}."
            return txt + _limit_note(shopper, it)
        if pms and intent == "pricing":
            pm = pms[0]
            return (f"{pm['competitor']} has {_short(pm['name'])} for {money(pm['competitor_price_usd'])}; your price is "
                    f"{money(pm['member_price_usd'])}. Check out with a price match to save {money(pm['saving_usd'])} - Customer "
                    "Care approves it and the order is placed automatically.")
        if intent == "availability":
            return ("I couldn't match that to an item in the catalog. Search the Shop tab - every item shows what's on hand "
                    "at your fulfillment center and its aisle at your home store, even offline.")
        return (f"You're a {tier} member: {shopper['membership']['discount_pct']}% off list on every item, plus multi-buy "
                "savings (3+ another 5%, 6+ another 10%). Every price in the app is already your price.")
    if intent == "rewards":
        w = d.get("wallet") or {}
        t = C.TIER_BY_CODE[shopper["membership"]["tier"]]
        return (f"You have {money(w.get('store_credit_usd', 0))} in store credit and {int(w.get('points', 0)):,} points. As a "
                f"{tier} member you get {t['discount_pct']}% off and {t['points_per_usd']} point"
                f"{'s' if t['points_per_usd'] > 1 else ''} per dollar"
                + (f"; your membership renews {shopper['membership']['renewal_date']}." if shopper["membership"].get("renewal_date") else ".")
                + " Store credit comes off your next order automatically.")
    if intent == "delivery":
        dl = shopper["delivery"]
        t = C.TIER_BY_CODE[shopper["membership"]["tier"]]
        lo = d.get("last_order")
        due = (f"Order {lo['order_id']} is due {lo['eta']} ({lo.get('delivery_option')}). "
               if lo and lo.get("eta") and "order" in message.lower() else "")
        return (due + f"Your default is {dl.get('default_option')} from the {C.FULFILLMENT_CENTERS[dl['fulfillment_center']]['name']}. "
                + ("Delivery is free on every order with your membership." if t["free_delivery_min_usd"] == 0
                   else f"Delivery is free over ${t['free_delivery_min_usd']}, otherwise ${C.DELIVERY_FEE_USD}.")
                + f" Store pickup at {(shopper.get('home_store') or {}).get('name')} is always free and usually ready in about two hours."
                + (f" Delivery note on file: {dl['notes']}." if dl.get("notes") else ""))
    if intent == "returns":
        lo = d.get("last_order")
        if lo and (days_since(lo["date"]) or 0) <= C.RETURN_WINDOW_DAYS:
            return (f"Your last order {lo['order_id']} ({lo['date']}) is inside the {C.RETURN_WINDOW_DAYS}-day window. Pick the "
                    "item below and the refund goes to your store credit right away; drop it at any returns desk within 14 "
                    "days. If something arrived damaged, I can request a goodwill credit instead - no return needed.")
        return f"Returns are accepted within {C.RETURN_WINDOW_DAYS} days of delivery, refunded to your store credit."
    if intent == "history":
        sp = d.get("spend") or {}
        lo = d.get("last_order")
        last = ""
        if lo:
            last = (f"Your last order {lo['order_id']} on {lo['date']} was {money(lo['total_usd'])}: "
                    f"{str(lo.get('status', '')).replace('_', ' ')}, {lo.get('delivery_option')}"
                    + (f", ETA {lo['eta']}" if lo.get("eta") else "")
                    + (f", with {len(lo['backordered'])} item backordered until {lo['backordered'][0].get('eta')}" if lo.get("backordered") else "")
                    + ". ")
        return (last + f"Over the last 12 months you've spent {money(sp.get('last_12mo_usd', 0))} across "
                f"{sp.get('orders_last_12mo', 0)} orders, about {money(sp.get('avg_order_usd', 0))} an order.")
    return "Here's what I found for you."


def suggest_actions(intent: str, ctx: dict, shopper: dict | None, message: str) -> list[dict]:
    if not shopper:
        return []
    facts = ctx.get("facts") or {}
    d = facts.get("data") or {}
    acts: list[dict] = []
    if intent == "reorder":
        lines = d.get("lines", [])
        if lines:
            payload = [{"sku": l["sku"], "qty": l["qty"]} for l in lines]
            t = d.get("totals") or {}
            acts.append({"label": f"Order my smart cart · {money(t.get('total_usd', 0))}", "tool": "place_order",
                         "args": {"lines": payload}})
            acts.append({"label": "Review in Cart", "screen": "cart"})
    elif intent in ("pricing", "availability"):
        items = facts.get("catalog") or []
        pms = d.get("price_matches") or []
        pm = next((p for p in pms if (items and p["sku"] == items[0]["sku"]) or (not items and intent == "pricing")), None)
        if pm:
            acts.append({"label": f"Buy with price match · {money(pm['competitor_price_usd'])}", "tool": "request_price_match",
                         "args": {"lines": [{"sku": pm["sku"], "qty": 1}], "sku": pm["sku"], "competitor": pm["competitor"],
                                  "competitor_price_usd": pm["competitor_price_usd"]}})
        if items:
            acts.append({"label": f"View {_short(items[0]['name'])}", "screen": "product", "args": {"sku": items[0]["sku"]}})
            up = C.UPGRADES.get(items[0]["sku"])
            if up:
                acts.append({"label": f"See {_short(C.PRODUCT_BY_SKU[up[0]]['name'])}", "screen": "product", "args": {"sku": up[0]}})
        else:
            acts.append({"label": "Open Cart", "screen": "cart"})
    elif intent == "deals":
        deals = d.get("deals") or []
        items = facts.get("catalog") or []
        if items and re.search(r"go(es)? (well )?with|pair|bundle", message.lower()):
            for x, _ in C.PAIRS_WITH.get(items[0]["sku"], [])[:2]:
                acts.append({"label": f"View {_short(C.PRODUCT_BY_SKU[x]['name'])}", "screen": "product", "args": {"sku": x}})
            return acts + [{"label": "Open Cart", "screen": "cart"}]
        top = next((x for x in deals if x.get("applies")), None)
        if top:
            acts.append({"label": f"View {_short(top['name'])}", "screen": "product", "args": {"sku": top["sku"]}})
        b = next((x for x in deals if x["kind"] == "bundle"), None)
        if b:
            acts.append({"label": f"See the {b['label']}", "screen": "product", "args": {"sku": b["skus"][0]}})
        acts.append({"label": "Open Cart", "screen": "cart"})
    elif intent == "rewards":
        acts.append({"label": "Open Cart", "screen": "cart"})
    elif intent == "subscription":
        lines = d.get("lines", [])
        it = next((i for i in facts.get("catalog") or [] if C.PRODUCT_BY_SKU[i["sku"]]["consumable"]), None)
        if it:
            qty = int((shopper.get("replenishment", {}).get(it["sku"]) or {}).get("last_qty") or 1)
            acts.append({"label": f"Subscribe · {qty} x {_short(it['name'])} / 4 wks", "tool": "set_subscription",
                         "args": {"sku": it["sku"], "qty": qty, "cadence_weeks": 4}})
        elif lines:
            top = lines[0]
            acts.append({"label": f"Subscribe · {top['qty']} x {_short(top['name'])} / 4 wks", "tool": "set_subscription",
                         "args": {"sku": top["sku"], "qty": top["qty"], "cadence_weeks": 4}})
    elif intent == "returns":
        lo = d.get("last_order")
        if lo and lo.get("lines") and (days_since(lo["date"]) or 0) <= C.RETURN_WINDOW_DAYS:
            l0 = lo["lines"][0]
            acts.append({"label": f"Return 1 x {_short(l0['name'])} from {lo['order_id']}", "tool": "create_return",
                         "args": {"order_id": lo["order_id"], "sku": l0["sku"], "qty": 1,
                                  "reason": f"Requested in the assistant: {message.strip()[:120]}"}})
        acts.append({"label": "Request $15 goodwill credit", "tool": "issue_goodwill_credit",
                     "args": {"amount_usd": 15, "reason": f"Service issue reported in the app: {message.strip()[:150]}"}})
    elif intent == "preferences":
        acts.append({"label": "Edit my preferences", "screen": "preferences"})
    elif intent == "history":
        acts.append({"label": "See my orders", "screen": "orders"})
    elif intent == "delivery":
        acts.append({"label": "Open Cart", "screen": "cart"})
    return acts


# Only durable things the shopper tells the assistant become memories ("we
# got a puppy", "I prefer unscented"). Plain questions don't, which also
# keeps repeat questions' prompts stable for the LLM cache.
STATEMENT_RE = re.compile(r"\b(i|we)\s+(prefer|like|love|hate|don't like|dont like|just got|got a|have a|switched|moved|only buy|"
                          r"never buy|always buy)\b|\bnote that\b|\bremember\b", re.I)


async def _remember(shopper_id: str, message: str, intent: str, session_id: str):
    if not aom.enabled or not STATEMENT_RE.search(message):
        return
    try:
        await aom.memory_add(shopper_id, f"Shopper said ({intent}): {message.strip()[:500]}", session_id=session_id,
                             metadata={"intent": intent, "app": "pos-mobile", "shopper_id": shopper_id})
    except AOMError as exc:
        log.info("memory add skipped: %s", exc)


async def _recall(shopper_id: str, message: str) -> list[str]:
    if not aom.enabled:
        return []
    try:
        res = await aom.memory_search(shopper_id, message, top_k=3)
        return [e.get("content", "") for e in res.get("entries", []) if e.get("content")]
    except AOMError:
        return []


async def _ask_rag_app(message: str, intent: str) -> dict | None:
    """Guide questions go to the assistant's AOM RAG Application: one call
    that runs AOM's Context Cache (retrieval), the KB search scoped to today's
    Shopping Guide, and the LLM Cache, and is counted on the RAG Applications page."""
    if not rag_app.usable():
        return None
    l0 = time.perf_counter()
    try:
        res = await aom.rag_query(config.RAG_APP_ID, message)
    except AOMError as exc:
        rag_app.query_failed(exc)
        log.info("RAG app query failed, answering on the assistant's own path: %s", exc)
        return None
    ms = round((time.perf_counter() - l0) * 1000, 1)
    retrieval = res.get("retrieval") or {}
    llm_res = res.get("llm") or {}
    cache = llm_res.get("cache") or {}
    srcs = res.get("sources") or []
    chunks = [{"title": c.get("document_title"), "content": c.get("preview"), "source": "aom-rag"} for c in srcs]
    reply = (res.get("answer") or "").strip()
    llm = {"status": cache.get("status", "skipped" if not res.get("llm") else "miss"), "similarity": cache.get("similarity"),
           "namespace": f"rag:{config.RAG_APP_ID}", "provider": llm_res.get("provider"), "model": llm_res.get("model"),
           "latency_ms": ms, "stub": bool(llm_res.get("stub")), "tokens_saved": llm_res.get("tokens_saved") or 0,
           "cost_saved_usd": llm_res.get("cost_saved_usd") or 0, "rag_app": config.RAG_APP_ID}
    if not res.get("llm") or llm["stub"] or not reply:
        reply = compose_fallback(intent, False, message, {"kb_guide": chunks}, None)
        llm["composed_locally"] = True
    return {
        "reply": reply, "llm": llm,
        "sources": [{"title": c["title"], "snippet": (c.get("content") or "")[:160], "kind": "aom-rag"} for c in chunks[:3]],
        "context": {"status": retrieval.get("cache", "miss"), "latency_ms": retrieval.get("latency_ms", 0),
                    "source": "aom-rag", "rag_app": config.RAG_APP_ID, "chunks": retrieval.get("chunks"),
                    "export_date": STATE.get("export_date"), "kb_mode": "aom"},
        "trace_id": (res.get("trace") or {}).get("trace_id"),
    }


async def ask(shopper_id: str, message: str, session_id: str | None = None) -> dict:
    session_id = session_id or f"app-{shopper_id}"
    trace_id = uuid.uuid4().hex
    token = current_trace.set({"trace_id": trace_id, "session_id": session_id})
    t0 = time.perf_counter()
    try:
        shopper = await store.get("shoppers", shopper_id_doc(shopper_id))
        if not shopper:
            raise ValueError("unknown shopper")
        intent, personal = classify(message)
        if not personal:
            via_rag = await _ask_rag_app(message, intent)
            if via_rag:
                result = {
                    "reply": via_rag["reply"], "intent": intent, "personal": False, "actions": [],
                    "sources": via_rag["sources"], "context": via_rag["context"], "memory_recalled": 0,
                    "llm": via_rag["llm"], "trace_id": via_rag["trace_id"] or trace_id, "governed": True,
                    "total_ms": round((time.perf_counter() - t0) * 1000, 1), "export_date": STATE.get("export_date"),
                }
                metrics.assistant(result)
                await _append_chat(shopper_id, message, result)
                return result
        ctx = await get_context(shopper, intent, personal, message)
        if personal and intent in ("pricing", "availability", "subscription", "deals"):
            ctx = dict(ctx, facts=dict(ctx.get("facts") or {}, catalog=await _catalog_lookup(shopper_id, message)))
        memories = await _recall(shopper_id, message) if personal else []
        prompt = build_prompt(message, personal, ctx, shopper, memories)
        namespace = config.LLM_NAMESPACE_SHOPPER if personal else config.LLM_NAMESPACE_GUIDE
        llm = {"status": "local", "namespace": namespace, "provider": None, "model": None, "similarity": None,
               "latency_ms": None, "stub": False, "tokens_saved": 0}
        reply = None
        if aom.enabled:
            try:
                l0 = time.perf_counter()
                res = await aom.complete(prompt, namespace=namespace)
                cache = res.get("cache") or {}
                llm.update({"status": cache.get("status", "miss"), "similarity": cache.get("similarity"),
                            "provider": res.get("provider"), "model": res.get("model"),
                            "latency_ms": round((time.perf_counter() - l0) * 1000, 1), "stub": bool(res.get("stub")),
                            "tokens_saved": res.get("tokens_saved", 0), "cost_saved_usd": res.get("cost_saved_usd", 0)})
                if not res.get("stub") and (res.get("response") or "").strip():
                    reply = res["response"].strip()
            except AOMError as exc:
                llm["status"] = "error"
                llm["error"] = str(exc)[:200]
        if not reply:
            reply = compose_fallback(intent, personal, message, ctx, shopper)
            llm["composed_locally"] = True
        actions = suggest_actions(intent, ctx, shopper, message) if personal else []
        sources = ([{"title": c["title"], "snippet": (c.get("content") or "")[:160], "kind": c.get("source")}
                    for c in (ctx.get("kb_profile", []) if personal else [])[:2]]
                   + [{"title": c["title"], "snippet": (c.get("content") or "")[:160], "kind": c.get("source")}
                      for c in ctx.get("kb_guide", [])[:2]])
        result = {
            "reply": reply, "intent": intent, "personal": personal, "actions": actions, "sources": sources,
            "context": {"status": ctx["_cache"]["status"], "latency_ms": ctx["_cache"]["latency_ms"],
                        "source": ctx["_cache"].get("source"),
                        "built_at": ctx.get("built_at"), "build_ms": ctx.get("build_ms"), "kb_mode": ctx.get("kb_mode"),
                        "export_date": ctx.get("export_date")},
            "memory_recalled": len(memories), "llm": llm, "trace_id": trace_id if aom.enabled else None,
            "governed": aom.enabled, "total_ms": round((time.perf_counter() - t0) * 1000, 1),
            "export_date": STATE.get("export_date"),
        }
        metrics.assistant(result)
        await _append_chat(shopper_id, message, result)
        if personal:
            await _remember(shopper_id, message, intent, session_id)
        return result
    finally:
        current_trace.reset(token)


async def _append_chat(shopper_id: str, message: str, result: dict):
    doc = await store.get("assistant", chat_id(shopper_id)) or {
        "type": "assistant", "shopper_id": shopper_id, "messages": [], "answers": {}, "channels": [shopper_channel(shopper_id)]}
    now = now_iso()
    doc["messages"] = (doc["messages"] + [
        {"id": uuid.uuid4().hex[:12], "role": "shopper", "text": message, "at": now},
        {"id": uuid.uuid4().hex[:12], "role": "assistant", "text": result["reply"], "at": now,
         "intent": result["intent"], "actions": result["actions"], "context": result["context"], "llm": result["llm"],
         "sources": result["sources"]},
    ])[-40:]
    # Answer cache the phone can reuse offline for the same (normalized) question.
    doc["answers"][_norm(message)[:120]] = {"reply": result["reply"], "intent": result["intent"], "at": now,
                                            "actions": result["actions"]}
    if len(doc["answers"]) > 60:
        doc["answers"] = dict(list(doc["answers"].items())[-60:])
    doc["updated_at"] = now
    await store.upsert("assistant", chat_id(shopper_id), doc)
