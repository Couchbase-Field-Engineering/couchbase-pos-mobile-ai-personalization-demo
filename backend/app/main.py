"""Backend for the Couchbase ShopSmart POS mobile AI personalization demo.

  /api/*            shopper sign-in (mints a Sync Gateway session), assistant,
                    status, and a REST "preview sync" used only by the browser
                    preview build
  /api/voice/*      the voice ordering agent (drive-thru lane, phone line)
  /api/kiosk/*      the self-checkout vision agent
  /api/pos/*        a store's edge state: WAN link, kitchen counts, tickets
  /pos/mcp/         the shopsmart-pos MCP server AOM governs

Background tasks: the Databricks export loader/poller and the offline-first
order worker (shopper orders from phones and tickets from stores).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import logging
import os
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import aom_bootstrap, assistant, config, loader, metrics, personalization, pos, pricing, rag_app, sgw, vision, voice, worker
from . import actions as actions_mod
from . import menu as menu_mod
from .aom_client import aom
from .docs import (CATALOG_ID, STATE, chat_id, ctx_id, now_iso, recs_id, shopper_channel, shopper_id_doc, store_channel,
                   store_id_doc)
from .mcp_server import mcp
from .store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pos.api")

PERSONA_BLURBS = {
    "S00101001": "Shops on a subway commute with no signal. Coffee pods are nearly sold out, and the stand mixer she keeps viewing is cheaper at a competitor: a price match that needs approval.",
    "S00101002": "Rural drive home with long dead zones. Two dogs: the dog food is backordered and belongs on Subscribe & Save.",
    "S00101003": "Household of five. Wants a second limited-release console (refused by the purchase limit) and a goodwill credit for a damaged delivery.",
}
_bg: list = []
_LAST_PULL: dict[str, str] = {}


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await store.connect()

    auto_register = config.AOM_AUTO_REGISTER and bool(config.AOM_ADMIN_PASSWORD)

    async def initial_load():
        try:
            # With self-registration the KB is published once AOM sign-in works.
            await loader.load(kb=not auto_register)
            metrics.export_loaded(loader.STATUS.get("rows", 0))
        except Exception:  # noqa: BLE001
            log.exception("initial export load failed")

    first = asyncio.create_task(initial_load())
    _bg.append(first)
    if auto_register:
        _bg.append(asyncio.create_task(aom_bootstrap.run(first)))
    _bg.append(asyncio.create_task(worker.loop()))
    _bg.append(asyncio.create_task(loader.poll_forever()))
    _bg.append(asyncio.create_task(pricing_loop()))
    async with mcp.session_manager.run():
        yield
    for t in _bg:
        t.cancel()


async def pricing_loop():
    """Re-evaluate the pricing rules on a timer: the hour changes, and with
    it which promotion windows are open."""
    while True:
        await asyncio.sleep(config.PRICING_REFRESH_SECONDS)
        if loader.STATUS.get("state") == "loaded":
            await pricing.refresh_all("timer")


app = FastAPI(title="Couchbase ShopSmart POS Mobile AI Personalization Demo", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
app.mount("/pos", mcp.streamable_http_app())


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
def _sign(payload: str) -> str:
    return hmac.new(config.APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(shopper_id: str) -> str:
    payload = f"{shopper_id}|{int(time.time()) + config.APP_TOKEN_TTL_SECONDS}"
    return base64.urlsafe_b64encode(f"{payload}|{_sign(payload)}".encode()).decode()


def current_shopper(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in first")
    try:
        rid, exp, sig = base64.urlsafe_b64decode(authorization.split(" ", 1)[1].encode()).decode().split("|")
    except Exception:  # noqa: BLE001
        raise HTTPException(401, "Invalid session") from None
    if not hmac.compare_digest(sig, _sign(f"{rid}|{exp}")) or int(exp) < time.time():
        raise HTTPException(401, "Session expired - sign in again")
    return rid


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    """Landing page: where everything in the demo lives."""
    rows = [
        ("https://localhost:5154/story", "The story: shopping that doesn't wait for signal, and a Databricks bill that stops growing per tap (start here)"),
        ("https://localhost:5154", "The app in a browser (docker compose service <code>web</code>)"),
        ("https://localhost:5154/lane", "Voice ordering: the drive-thru lane and the store phone line, with the voice agent"),
        ("https://localhost:5154/checkout", "Self-checkout: the kiosk with the vision agent, and the attendant's exceptions"),
        ("https://localhost:5154/merch", "Merchandising console: live pricing rules, bundles and the levers that move them"),
        ("http://localhost:8204/api/status", "Backend status: export load, Knowledge Base publishing, AOM, Sync Gateway, Databricks offload"),
        ("http://localhost:8204/docs", "Backend API (OpenAPI)"),
        ("http://localhost:8204/pos/mcp", "MCP server <code>shopsmart-pos</code> (called by AOM)"),
        ("http://localhost:18095", "Couchbase Web Console (Administrator / password123)"),
        ("ws://localhost:4992/pos", "Sync Gateway - Couchbase Lite replicates here"),
        ("https://localhost:8090", "Couchbase Agent Operations Manager (its own stack)"),
    ]
    items = "".join(f'<li><a href="{u}">{u}</a> &nbsp;{d}</li>' for u, d in rows)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>ShopSmart POS demo backend</title>
<style>body{{font-family:-apple-system,"Helvetica Neue",Arial,sans-serif;max-width:760px;margin:48px auto;padding:0 16px;color:#0b0b0c}}
h1{{color:#ea2328}} li{{margin:10px 0}} a{{color:#ea2328;font-weight:600}}</style></head>
<body><h1>Couchbase ShopSmart POS demo</h1>
<p>This is the backend. Open the app at <a href="https://localhost:5154">https://localhost:5154</a>.
Loaded export: <b>{STATE.get("export_date") or "loading..."}</b> ({loader.STATUS.get("state")}).</p>
<ul>{items}</ul></body></html>"""


# Run from a checkout (the 2-minute preview), the backend serves the consoles itself. In Docker and
# Kubernetes the image has no web/ folder and the `web` container serves them.
_WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "web"))
for _page in ("lane", "checkout", "merch", "story"):
    if os.path.isfile(os.path.join(_WEB_DIR, f"{_page}.html")):
        app.add_api_route(f"/{_page}", (lambda path: (lambda: FileResponse(path, media_type="text/html")))(
            os.path.join(_WEB_DIR, f"{_page}.html")), include_in_schema=False)


@app.get("/api/health")
async def health():
    return {"status": "ok", "export_date": STATE.get("export_date"), "load": loader.STATUS.get("state")}


@app.get("/api/status")
async def status():
    aom_state = {"configured": aom.enabled, "url": config.AOM_URL or None, "kb_publisher": aom.admin_enabled,
                 "last_error": aom.last_error, "registration": aom_bootstrap.STATUS, "rag_app": dict(rag_app.STATUS, app_id=config.RAG_APP_ID),
                 "context_namespace": config.CONTEXT_NAMESPACE,
                 "dashboard_url": config.AOM_DASHBOARD_URL or None}
    if aom.enabled:
        try:
            aom_state["health"] = await aom.health()
        except Exception as exc:  # noqa: BLE001
            aom_state["health"] = {"error": str(exc)[:200]}
    return {"store": store.driver, "export_date": STATE.get("export_date"),
            "load": {k: v for k, v in loader.STATUS.items()}, "aom": aom_state, "metrics": metrics.snapshot(),
            "pricing": dict(pricing.STATS, refresh_seconds=config.PRICING_REFRESH_SECONDS,
                            hour=pricing.hour_now(await pricing.get_signals()), recent_changes=list(pricing.CHANGES)[:8]),
            "sync_gateway": {"configured": sgw.enabled(), "public_url": config.SG_PUBLIC_URL or None, "db": config.SG_DB},
            "mcp_server": {"server_id": config.AOM_MCP_SERVER_ID, "registered_url": config.AOM_MCP_PUBLIC_URL}}


@app.get("/api/demo/personas")
async def personas():
    out = []
    for sid in loader.PERSONAS:
        sh = await store.get("shoppers", shopper_id_doc(sid))
        if not sh:
            continue
        recs = await store.get("recommendations", recs_id(sid)) or {}
        out.append({"shopper_id": sid, "name": f"{sh['first_name']} {sh['last_name']}", "last_name": sh["last_name"],
                    "first_name": sh["first_name"], "store_id": _store_of(sh),
                    "member_number": sh["member_number"], "city": f"{sh['address']['city']}, {sh['address']['state']}",
                    "tier": sh["membership"]["tier_name"], "smart_cart_items": len([l for l in recs.get("lines", []) if l.get("selected")]),
                    "home_store": (sh.get("home_store") or {}).get("name"), "blurb": PERSONA_BLURBS.get(sid, "")})
    return {"personas": out, "export_date": STATE.get("export_date")}


class LoginRequest(BaseModel):
    member_number: str
    last_name: str


def _store_of(shopper: dict) -> str | None:
    return (shopper.get("home_store") or {}).get("store_id")


async def _session(shopper: dict) -> dict:
    sid = shopper["shopper_id"]
    # Recommendations are pre-built for recently active shoppers; everyone else gets theirs here.
    if not await store.get("recommendations", recs_id(sid)):
        await personalization.refresh_recommendations(sid)
    return {"token": make_token(sid), "shopper_id": sid,
            "sync_gateway": await sgw.mint_session(sid, _store_of(shopper), shopper["delivery"]["fulfillment_center"]),
            "export_date": STATE.get("export_date")}


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    idx = await store.get("lookups", f"login::{req.member_number.strip()}")
    if not idx or req.last_name.strip().lower() != idx.get("last_name"):
        raise HTTPException(401, "We couldn't find that member number and last name.")
    shopper = await store.get("shoppers", shopper_id_doc(idx["shopper_id"]))
    sid = shopper["shopper_id"]
    return {**await _session(shopper),
            "shopper": {"first_name": shopper["first_name"], "last_name": shopper["last_name"], "email": shopper.get("email"),
                        "tier": shopper["membership"]["tier"], "tier_name": shopper["membership"]["tier_name"],
                        "home_store": shopper.get("home_store")},
            "channels": [shopper_channel(sid), store_channel(_store_of(shopper) or "-"),
                         pricing.fc_channel(shopper["delivery"]["fulfillment_center"]), "catalog"]}


@app.post("/api/auth/refresh")
async def refresh(shopper_id: str = Depends(current_shopper)):
    """Re-mint the Sync Gateway session (24h) for a signed-in shopper - called
    by the app on launch when it has connectivity."""
    shopper = await store.get("shoppers", shopper_id_doc(shopper_id))
    if not shopper:
        raise HTTPException(401, "Sign in again")
    return await _session(shopper)


@app.get("/api/me")
async def me(shopper_id: str = Depends(current_shopper)):
    return await store.get("shoppers", shopper_id_doc(shopper_id))


async def _shopper_docs(shopper_id: str) -> list[dict]:
    out = []
    shopper = await store.get("shoppers", shopper_id_doc(shopper_id))
    if not shopper:
        return out
    out.append({"collection": "shoppers", "id": shopper_id_doc(shopper_id), "doc": shopper})
    sid = _store_of(shopper)
    pairs = [("recommendations", recs_id(shopper_id)), ("assistant", chat_id(shopper_id)), ("catalog", CATALOG_ID),
             ("catalog", pricing.pricing_id(shopper["delivery"]["fulfillment_center"]))]
    if sid:
        pairs.append(("stores", store_id_doc(sid)))
    pairs += [("context_cache", ctx_id(shopper_id, key)) for key in assistant.CONTEXT_KEYS]
    for col, key in pairs:
        d = await store.get(col, key)
        if d:
            out.append({"collection": col, "id": key, "doc": d})
    for d in await store.orders_for_shopper(shopper_id):
        key = d.pop("_id")
        out.append({"collection": "orders", "id": key, "doc": d})
    return out


@app.get("/api/sync/pull")
async def sync_pull(shopper_id: str = Depends(current_shopper)):
    """Browser-preview stand-in for Sync Gateway: the same documents and ids
    the shopper's channels would replicate into Couchbase Lite."""
    docs = await _shopper_docs(shopper_id)
    # Count only what changed since this shopper's last pull, the way a
    # Couchbase Lite replicator only moves changed revisions - a poll that
    # finds nothing new is not a warehouse query avoided.
    fp = hashlib.sha1("|".join(f"{d['id']}:{d['doc'].get('updated_at') or d['doc'].get('generated_at') or d['doc'].get('rev', '')}:{d['doc'].get('status', '')}"
                               for d in docs).encode()).hexdigest()
    if _LAST_PULL.get(shopper_id) != fp:
        metrics.sync_pull(len(docs) if shopper_id not in _LAST_PULL else max(1, len(docs) // 10))
        _LAST_PULL[shopper_id] = fp
    return {"docs": docs, "at": now_iso(), "export_date": STATE.get("export_date")}


class PushDoc(BaseModel):
    collection: str
    id: str
    doc: dict


class PushRequest(BaseModel):
    docs: list[PushDoc] = Field(default_factory=list)


@app.post("/api/sync/push")
async def sync_push(req: PushRequest, shopper_id: str = Depends(current_shopper)):
    """Browser-preview stand-in for a push replication. Same rule the Sync
    Gateway sync function enforces: a shopper may only create `orders`
    documents, only as themselves, only as `pending`."""
    accepted = []
    for d in req.docs:
        if d.collection != "orders" or d.doc.get("shopper_id") != shopper_id or not d.id.startswith(f"order::{shopper_id}::"):
            raise HTTPException(403, "Only your own orders can be pushed")
        if d.doc.get("origin"):
            raise HTTPException(403, "Only a store device can write a store ticket")
        if await store.get("orders", d.id):
            continue
        doc = {**d.doc, "type": "request", "status": "pending", "channels": [shopper_channel(shopper_id)],
               "received_at": now_iso()}
        await store.upsert("orders", d.id, doc)
        accepted.append(d.id)
    if accepted:
        _bg.append(asyncio.create_task(worker.run_once()))
    return {"accepted": accepted}


class AskRequest(BaseModel):
    message: str
    session_id: str | None = None


@app.post("/api/assistant")
async def ask(req: AskRequest, shopper_id: str = Depends(current_shopper)):
    if not req.message.strip():
        raise HTTPException(400, "Ask me something")
    try:
        return await assistant.ask(shopper_id, req.message[:1000], req.session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


# --------------------------------------------------------------------------
# Merchandising console (web/merch.html). Demo levers, like /api/admin/reload:
# no sign-in, so keep this backend off the public internet.
# --------------------------------------------------------------------------
async def _merch_state(fc: str) -> dict:
    from . import catalog as C
    from .actions import get_inventory
    if fc not in C.FULFILLMENT_CENTERS:
        raise HTTPException(404, "Unknown fulfillment center")
    doc = await pricing.refresh(fc, "console")
    signals = await pricing.get_signals()
    hour = pricing.hour_now(signals)
    inv, _ = await get_inventory(fc)
    rows = []
    for p in C.PRODUCTS:
        sku = p["sku"]
        line = pricing.quote("MEMBER", {sku: 1}, doc, hour)[0]
        mp = (signals.get("market_prices") or {}).get(sku)
        rows.append({"sku": sku, "name": p["name"], "category": C.CATEGORIES[p["category"]]["name"], "list_price": p["list_price"],
                     "shelf_price": line["unit_price"], "adjustments": line["adjustments"],
                     "on_hand": int(inv["on_hand"].get(sku, 0)), "level": C.stock_level(sku),
                     "rules": doc["sku_rules"].get(sku, {}), "competitor": mp})
    return {"fc": fc, "fc_name": doc["fc_name"], "fulfillment_centers": {k: v["name"] for k, v in C.FULFILLMENT_CENTERS.items()},
            "version": doc["version"], "generated_at": doc.get("generated_at"), "hour": hour, "clock": signals.get("clock"),
            "products": rows,
            "time_rules": [dict(w, active=pricing.window_active(w, hour)) for w in doc["time_rules"]],
            "bundles": doc["bundles"], "changes": [c for c in pricing.CHANGES if c["fc"] == fc][:25],
            "engine": dict(pricing.STATS, refresh_seconds=config.PRICING_REFRESH_SECONDS, signals_source=signals.get("source")),
            "merchandising": metrics.snapshot()["merchandising"], "competitors": C.COMPETITORS,
            "auto_match_min_pct_of_list": C.AUTO_MATCH_MIN_PCT_OF_LIST, "export_date": STATE.get("export_date")}


@app.get("/api/merch/state")
async def merch_state(fc: str = "FC-NE"):
    return await _merch_state(fc)


class Simulate(BaseModel):
    fc: str = "FC-NE"
    action: str                 # set_inventory | set_hour | set_competitor_price | reset
    sku: str | None = None
    value: float | None = None
    competitor: str | None = None


@app.post("/api/merch/simulate")
async def merch_simulate(req: Simulate):
    """Change one of the engine's inputs and re-price at once: stock at a
    fulfillment center, the hour, or a competitor's price."""
    from . import catalog as C
    from .actions import get_inventory
    from .docs import inventory_id
    if req.fc not in C.FULFILLMENT_CENTERS:
        raise HTTPException(404, "Unknown fulfillment center")
    signals = await pricing.get_signals()
    everywhere = False
    if req.action == "set_inventory":
        if req.sku not in C.PRODUCT_BY_SKU or req.value is None or req.value < 0:
            raise HTTPException(400, "set_inventory needs a sku and a value of 0 or more")
        inv, _ = await get_inventory(req.fc)
        inv["on_hand"][req.sku] = int(req.value)
        inv["updated_at"] = now_iso()
        await store.upsert("inventory", inventory_id(req.fc), inv)
    elif req.action == "set_hour":
        hour = None if req.value is None else int(req.value) % 24
        signals["clock"] = {**(signals.get("clock") or {}), "tz": config.PRICING_TZ, "demo_hour": hour}
        await pricing.set_signals(signals)
        everywhere = True
    elif req.action == "set_competitor_price":
        if req.sku not in C.PRODUCT_BY_SKU:
            raise HTTPException(400, "set_competitor_price needs a sku")
        prices = signals.setdefault("market_prices", {})
        if req.value is None:
            prices.pop(req.sku, None)
        else:
            prices[req.sku] = {"competitor": req.competitor if req.competitor in C.COMPETITORS else C.COMPETITORS[0],
                               "price_usd": round(float(req.value), 2)}
        await pricing.set_signals(signals)
        everywhere = True
    elif req.action == "reset":
        await store.upsert("inventory", inventory_id(req.fc),
                           {"type": "inventory", "fc": req.fc, "fc_name": C.FULFILLMENT_CENTERS[req.fc]["name"],
                            "on_hand": C.initial_inventory(req.fc), "created_at": now_iso()})
        fresh = pricing.default_signals()
        fresh.update({k: signals[k] for k in ("source",) if k in signals})
        fresh["clock"] = {"tz": config.PRICING_TZ, "demo_hour": config.DEMO_HOUR}
        await pricing.set_signals(fresh)
        everywhere = True
    else:
        raise HTTPException(400, "Unknown action")
    why = f"console: {req.action}" + (f" {req.sku}" if req.sku else "")
    if everywhere:
        await pricing.refresh_all(why)
    else:
        await pricing.refresh(req.fc, why)
    # The demo shoppers' "for you today" notes quote prices and deals: rebuild them so the phones follow at once.
    for sid in loader.PERSONAS:
        sh = await store.get("shoppers", shopper_id_doc(sid))
        if sh and (everywhere or sh["delivery"]["fulfillment_center"] == req.fc):
            await personalization.refresh_recommendations(sid)
            await assistant.invalidate_context(sid)
    return await _merch_state(req.fc)


# --------------------------------------------------------------------------
# The store: voice ordering (web/lane.html) and self-checkout (web/checkout.html).
# Demo consoles, like the merchandising console: no sign-in, so keep this
# backend off the public internet. In a store these calls are made by the lane
# controller and the kiosk, on the store's network.
# --------------------------------------------------------------------------
def _bad(exc: Exception):
    raise HTTPException(400, str(exc)) from None


@app.get("/api/pos/stores")
async def pos_stores():
    from . import catalog as C
    return {"stores": [{"store_id": s["store_id"], "name": s["name"], "city": s["city"], "state": s["state"]} for s in C.STORES.values()],
            "channels": pos.CHANNELS, "export_date": STATE.get("export_date")}


@app.get("/api/pos/store/{store_id}")
async def pos_store(store_id: str):
    try:
        view = await pos.store_view(store_id)
    except Exception as exc:  # noqa: BLE001
        _bad(exc)
    return {**view, "metrics": metrics.snapshot()["pos"], "hour": pricing.hour_now(await pricing.get_signals()),
            "aom": {"configured": aom.enabled, "dashboard_url": config.AOM_DASHBOARD_URL or None}}


@app.get("/api/pos/store/{store_id}/menu")
async def pos_menu(store_id: str, shopper_id: str = ""):
    """The menu board: every item priced right now at this store (for a member when shopper_id is given)."""
    try:
        sh = await store.get("shoppers", shopper_id_doc(shopper_id)) if shopper_id else None
        ctx = await pos.context(store_id, sh)
    except Exception as exc:  # noqa: BLE001
        _bad(exc)
    items = []
    for m in menu_mod.MENU:
        line = pos.price(ctx, [{"sku": m["sku"], "qty": 1}])[0]
        items.append({"sku": m["sku"], "name": m["name"], "group": m["group"], "sizes": m.get("sizes"), "list_price": line["list_price"],
                      "price": line["unit_price"], "adjustments": [a for a in line["adjustments"] if a["rule"] != "tier"],
                      "sold_out": line["sold_out"], "mods": [menu_mod.MODIFIERS[x]["label"] for x in m["mods"]]})
    return {"store_id": store_id, "hour": ctx["hour"], "items": items, "combos": menu_mod.COMBOS,
            "time_rules": [w for w in ctx["pricing"].get("time_rules", []) if pricing.window_active(w, ctx["hour"])]}


class StoreSimulate(BaseModel):
    action: str                 # wan_down | wan_up | set_kitchen | reset
    sku: str | None = None
    value: float | None = None


@app.post("/api/pos/store/{store_id}/simulate")
async def pos_simulate(store_id: str, req: StoreSimulate):
    """The store's levers: cut or restore the WAN link, change a kitchen count."""
    try:
        if req.action in ("wan_down", "wan_up"):
            await pos.set_wan(store_id, req.action == "wan_up")
        elif req.action == "set_kitchen":
            await pos.set_kitchen(store_id, req.sku or "", int(req.value or 0))
        elif req.action == "reset":
            await pos.reset(store_id)
        else:
            raise ValueError("Unknown action")
    except Exception as exc:  # noqa: BLE001
        _bad(exc)
    return await pos_store(store_id)


class VoiceStart(BaseModel):
    store_id: str
    channel: str = "drive_thru"
    lane: str = ""
    shopper_id: str = ""
    member_number: str = ""
    identified_by: str = ""


class VoiceTurn(BaseModel):
    text: str


@app.post("/api/voice/session")
async def voice_start(req: VoiceStart):
    try:
        return await voice.start(req.store_id, req.channel, req.lane, req.shopper_id, req.member_number, req.identified_by)
    except ValueError as exc:
        _bad(exc)


@app.post("/api/voice/session/{session_id}/turn")
async def voice_turn(session_id: str, req: VoiceTurn):
    if not req.text.strip():
        raise HTTPException(400, "Say something")
    try:
        return await voice.turn(session_id, req.text)
    except ValueError as exc:
        _bad(exc)


@app.get("/api/voice/session/{session_id}")
async def voice_get(session_id: str):
    try:
        return await voice.view(await voice.load(session_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


class KioskStart(BaseModel):
    store_id: str
    kiosk: str = ""
    shopper_id: str = ""
    member_number: str = ""


class KioskEvent(BaseModel):
    kind: str                   # place | bag | answer | remove | add_suggestion | loyalty | pay
    data: dict = Field(default_factory=dict)


class KioskResolve(BaseModel):
    decision: str               # charge_as_seen | accept_scan | remove
    attendant: str = ""
    note: str = ""


@app.post("/api/kiosk/session")
async def kiosk_start(req: KioskStart):
    try:
        return await vision.start(req.store_id, req.kiosk, req.shopper_id, req.member_number)
    except ValueError as exc:
        _bad(exc)


@app.get("/api/kiosk/session/{session_id}")
async def kiosk_get(session_id: str):
    try:
        return await vision.view(await vision.load(session_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@app.post("/api/kiosk/session/{session_id}/event")
async def kiosk_event(session_id: str, req: KioskEvent):
    try:
        return await vision.event(session_id, req.kind, req.data)
    except (ValueError, actions_mod.ToolError) as exc:
        _bad(exc)


@app.post("/api/kiosk/session/{session_id}/exceptions/{exception_id}/resolve")
async def kiosk_resolve(session_id: str, exception_id: str, req: KioskResolve):
    try:
        return await vision.request_resolution(session_id, exception_id, req.decision, req.attendant, req.note)
    except ValueError as exc:
        _bad(exc)


@app.get("/api/kiosk/shelf")
async def kiosk_shelf(store_id: str):
    from . import catalog as C
    if store_id not in C.STORES:
        raise HTTPException(404, "Unknown store")
    return {"store_id": store_id, "items": vision.shelf(store_id), "index": {"id": vision.INDEX_ID, "dim": vision.DIM, "metric": "cosine"}}


@app.post("/api/admin/reload")
async def reload_dump(force: bool = True):
    return {k: v for k, v in (await loader.load(force=force)).items()}
