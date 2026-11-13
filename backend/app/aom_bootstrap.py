"""Self-registration with AOM, for deployments with no one to run
scripts/register_with_aom.py (the Helm chart).

With AOM_AUTO_REGISTER=true and the AOM admin password in the environment,
the backend does on startup what the register script does by hand:

1. Signs in as the AOM admin. On a fresh AOM, whose admin account has no
   password yet, it sets AOM_ADMIN_PASSWORD as that password through AOM's
   first-login endpoint (POST /v1/auth/bootstrap).
2. Registers its own MCP server `shopsmart-pos` (or re-ingests it), at
   AOM_MCP_PUBLIC_URL, the URL AOM uses to reach this backend.
3. Issues the three agent identities in agents.py - the guest app's
   assistant, the voice ordering agent and the self-checkout vision agent -
   each scoped to its own tools, and keeps their API keys in Couchbase
   (`lookups::aom_agent*`) so a restart reuses them. If an agent already
   exists and its key isn't known here, the key is rotated.
4. Puts price matches, goodwill credits and self-checkout exceptions behind
   AOM's human-approval tier.
5. Publishes the Knowledge Base, which also registers or re-scopes the RAG
   application (see rag_app.py).

Every step is idempotent and retried until AOM is reachable, so the backend
and AOM can start in any order.
"""
from __future__ import annotations

import asyncio
import logging

from . import config, loader
from .aom_client import AOMError, aom
from .store import store

log = logging.getLogger("pos.aom_bootstrap")

from .actions import APPROVAL_TOOLS
from .actions import TOOLS as _TOOLS
from .agents import AGENTS, OWNER

AGENT_NAME = AGENTS["assistant"]["name"]
TOOLS = list(_TOOLS)
KEY_DOC = AGENTS["assistant"]["key_doc"]

STATUS: dict = {"state": "off"}


async def _sign_in():
    try:
        await aom.admin_login()
        return
    except AOMError as exc:
        # AOM answers 409 "no password set yet - use the setup form" on a fresh install.
        if exc.status not in (400, 401, 403, 409):
            raise
    # Fresh AOM: the admin account has no password until someone sets one.
    r = await aom._client.post(f"{aom.url}/v1/auth/bootstrap", json={"password": config.AOM_ADMIN_PASSWORD})
    if r.status_code == 200:
        log.info("Set the AOM admin password (first login) for user %s", config.AOM_ADMIN_USERNAME)
        aom._admin_cookies = r.cookies
        return
    raise AOMError(f"AOM admin sign-in failed and first-login setup refused: HTTP {r.status_code} {r.text[:200]}",
                   r.status_code)


async def _register_server():
    server = config.AOM_MCP_SERVER_ID
    try:
        res = await aom.admin("POST", "/v1/servers", {
            "server_id": server, "label": "ShopSmart POS", "owner": OWNER,
            "mcp_url": config.AOM_MCP_PUBLIC_URL, "trust_status": "trusted",
            "default_allowed_roles": ["sales_agent", "admin"],
        })
        if res.get("ingest_error"):
            raise AOMError(f"AOM could not reach {config.AOM_MCP_PUBLIC_URL}: {res['ingest_error']}")
        return res.get("ingested_tools")
    except AOMError as exc:
        if exc.status != 409:
            raise
    res = await aom.admin("POST", f"/v1/servers/{server}/reingest")
    return res.get("ingested_tools")


async def _key_works(key: str) -> bool:
    saved = aom.key
    aom.key = key
    try:
        await aom.discover("order", top_k=1)
        return True
    except AOMError as exc:
        return exc.status not in (401, 403)
    finally:
        aom.key = saved


async def _ensure_agent(agent: str = "assistant") -> str:
    """Issue (or recover the key of) one of the demo's agent identities."""
    spec = AGENTS[agent]
    from_env = getattr(config, spec["env"], "")
    if from_env:
        return from_env
    stored = await store.get("lookups", spec["key_doc"]) or {}
    if stored.get("api_key") and await _key_works(stored["api_key"]):
        return stored["api_key"]
    agents = (await aom.admin("GET", "/v1/agents")).get("agents", [])
    existing = next((a for a in agents if a.get("name") == spec["name"] and a.get("status") == "active"), None)
    if existing:
        res = await aom.admin("POST", f"/v1/agents/{existing['agent_id']}/rotate", {"revoke_immediately": True})
        agent_id = existing["agent_id"]
        log.info("Rotated the key of existing AOM agent %s (%s)", agent_id, spec["name"])
    else:
        res = await aom.admin("POST", "/v1/agents", {
            "name": spec["name"], "role": spec["role"], "owner": OWNER, "description": spec["description"],
            "allowed_tools": [f"{config.AOM_MCP_SERVER_ID}::{t}" for t in spec["tools"]],
        })
        agent_id = res["agent"]["agent_id"]
        log.info("Issued AOM agent identity %s (%s)", agent_id, spec["name"])
    await store.upsert("lookups", spec["key_doc"], {"type": "aom_agent", "agent_id": agent_id, "api_key": res["api_key"]})
    return res["api_key"]


async def _approval_tier():
    try:
        cfg = (await aom.admin("GET", "/v1/approvals-config"))["config"]
    except AOMError as exc:
        log.info("approval config unavailable: %s", exc)
        return
    want = {f"{config.AOM_MCP_SERVER_ID}::{t}" for t in APPROVAL_TOOLS}
    if cfg.get("enabled") and want <= set(cfg.get("require_for_tools") or []):
        return
    cfg["enabled"] = True
    cfg["require_for_tools"] = sorted(set(cfg.get("require_for_tools") or []) | want)
    await aom.admin("PUT", "/v1/approvals-config", {"config": cfg})


async def run(initial_load: asyncio.Task | None = None):
    if not (config.AOM_AUTO_REGISTER and aom.url and config.AOM_ADMIN_PASSWORD):
        return
    if initial_load:
        await asyncio.gather(initial_load, return_exceptions=True)
    await asyncio.sleep(2)  # let the MCP endpoint start serving before AOM ingests it
    delay = 5
    while True:
        try:
            STATUS.update(state="signing_in", error=None)
            await _sign_in()
            STATUS.update(state="registering_server")
            tools = await _register_server()
            STATUS.update(state="issuing_agent", tools=tools)
            aom.key = await _ensure_agent("assistant")
            for name in ("voice", "vision"):
                aom.agent_keys[name] = await _ensure_agent(name)
            STATUS.update(state="approval_tier")
            await _approval_tier()
            STATUS.update(state="registered")
            log.info("Registered with AOM at %s (%s tools)", aom.url, tools)
            break
        except Exception as exc:  # noqa: BLE001 - keep retrying until AOM is up
            STATUS.update(state="retrying", error=str(exc)[:300])
            log.warning("AOM registration not done yet (%s); retrying in %ss", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
    # Knowledge Base + RAG application, now that the admin login works.
    state = await store.get("lookups", "load_state") or {}
    if config.KB_ENABLED and state.get("export_date"):
        await loader.publish_kb(state["export_date"], await loader._kb_window())
