"""Client for the Couchbase Agent Operations Manager (AOM).

Two identities:

* The **agent** identity (`AOM_AGENT_API_KEY`, issued by
  scripts/register_with_aom.py via POST /v1/agents) - everything the
  assistant does at runtime: Knowledge Base retrieval, memory, cached LLM
  completions, and tool invocation. Every call shows up in AOM's audit log,
  traces and topology as the "ShopSmart POS Assistant" agent.
* An **operator** dashboard session (`AOM_ADMIN_USERNAME`/`PASSWORD`) - only
  the daily loader uses it, to publish Knowledge Base documents, because AOM
  deliberately makes adding retrievable knowledge an operator action.

AOM serves HTTPS with a self-signed certificate by default; TLS verification
is off for this client only (AOM_VERIFY_TLS=true turns it back on).
"""
from __future__ import annotations

import contextvars
import logging
import time

import httpx

from . import config

log = logging.getLogger("pos.aom")

# Set per assistant turn / per request so every AOM call made while answering
# one question (KB retrieval, memory, tool calls, the LLM) lands in ONE run
# on AOM's Traces page.
current_trace: contextvars.ContextVar[dict | None] = contextvars.ContextVar("aom_trace", default=None)
# Which of the demo's agents is calling (agents.py): "assistant", "voice" or
# "vision". Picks the API key and the agent id AOM attributes the call to.
current_agent: contextvars.ContextVar[str] = contextvars.ContextVar("aom_agent", default="assistant")


class AOMError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


class PendingApproval(Exception):
    def __init__(self, approval: dict, trace_id: str | None):
        super().__init__("pending approval")
        self.approval = approval
        self.trace_id = trace_id


class AOMClient:
    def __init__(self):
        self.url = config.AOM_URL
        self.key = config.AOM_AGENT_API_KEY
        self.agent_keys: dict[str, str] = {"voice": config.AOM_VOICE_AGENT_API_KEY, "vision": config.AOM_VISION_AGENT_API_KEY}
        self._client = httpx.AsyncClient(verify=config.AOM_VERIFY_TLS, timeout=config.AOM_TIMEOUT_SECONDS)
        self._admin_cookies: httpx.Cookies | None = None
        self.last_error: str | None = None
        self.last_ok: float | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    @property
    def admin_enabled(self) -> bool:
        return bool(self.url and config.AOM_ADMIN_PASSWORD)

    def _headers(self, trace_id: str | None = None):
        from .agents import AGENTS
        agent = current_agent.get()
        h = {"Authorization": f"Bearer {self.agent_keys.get(agent) or self.key}", "Content-Type": "application/json",
             "X-AOM-Agent-Id": AGENTS.get(agent, AGENTS["assistant"])["header_id"]}
        ctx = current_trace.get() or {}
        trace_id = trace_id or ctx.get("trace_id")
        if trace_id:
            h["X-AOM-Trace-Id"] = trace_id
        if ctx.get("session_id"):
            h["X-AOM-Session-Id"] = ctx["session_id"]
        return h

    async def _post(self, path, body, trace_id=None):
        try:
            r = await self._client.post(f"{self.url}{path}", json=body, headers=self._headers(trace_id))
        except httpx.HTTPError as exc:
            self.last_error = f"{path}: {exc}"
            raise AOMError(f"AOM unreachable: {exc}") from exc
        if r.status_code == 202:
            data = r.json()
            raise PendingApproval(data.get("approval") or {}, (data.get("trace") or {}).get("trace_id"))
        if r.status_code >= 400:
            self.last_error = f"{path}: HTTP {r.status_code} {r.text[:200]}"
            raise AOMError(f"AOM {path} -> HTTP {r.status_code}: {r.text[:300]}", r.status_code, r.text)
        self.last_ok = time.time()
        return r.json()

    async def _get(self, path, params=None):
        try:
            r = await self._client.get(f"{self.url}{path}", params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise AOMError(f"AOM unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise AOMError(f"AOM {path} -> HTTP {r.status_code}: {r.text[:300]}", r.status_code, r.text)
        return r.json()

    # ---- agent surface -------------------------------------------------------
    async def complete(self, prompt: str, namespace: str, bypass_cache=False, params=None) -> dict:
        body = {"prompt": prompt, "namespace": namespace, "bypass_cache": bypass_cache, "params": params or {}}
        if config.AOM_LLM_PROVIDER:
            body["provider"] = config.AOM_LLM_PROVIDER
        if config.AOM_LLM_MODEL:
            body["model"] = config.AOM_LLM_MODEL
        return await self._post("/v1/llm/complete", body)

    async def knowledge_search(self, query: str, top_k=4, document_id: str | None = None) -> dict:
        body = {"query": query, "top_k": top_k}
        if document_id:
            body["document_id"] = document_id
        return await self._post("/v1/agent/knowledge/search", body)

    async def memory_add(self, user_id, content, session_id=None, memory_type=None, metadata=None) -> dict:
        body = {"user_id": user_id, "content": content, "session_id": session_id, "metadata": metadata or {}}
        if memory_type:
            body["memory_type"] = memory_type
        return await self._post("/v1/memory", body)

    async def memory_search(self, user_id, query, top_k=4) -> dict:
        return await self._post("/v1/memory/search", {"user_id": user_id, "query": query, "top_k": top_k})

    async def invoke(self, tool_id: str, arguments: dict, approval_id: str | None = None) -> dict:
        body = {"tool_id": tool_id, "arguments": arguments}
        if approval_id:
            body["approval_id"] = approval_id
        return await self._post("/v1/tools/invoke", body)

    async def discover(self, query: str, top_k=5) -> dict:
        return await self._post("/v1/tools/discover", {"query": query, "top_k": top_k})

    # ---- AOM Context Cache (POST /v1/context/get|set) -------------------------
    async def context_get(self, key: str, namespace: str) -> dict:
        return await self._post("/v1/context/get", {"key": key, "namespace": namespace})

    async def context_set(self, key: str, value, namespace: str, ttl_seconds: int | None = None,
                          source_latency_ms: int | None = None) -> dict:
        return await self._post("/v1/context/set", {"key": key, "value": value, "namespace": namespace,
                                                    "ttl_seconds": ttl_seconds, "source_latency_ms": source_latency_ms})

    # ---- AOM RAG Applications ---------------------------------------------------
    async def rag_query(self, app_id: str, question: str, top_k: int | None = None, bypass_cache=False) -> dict:
        body = {"question": question, "bypass_cache": bypass_cache}
        if top_k:
            body["top_k"] = top_k
        return await self._post(f"/v1/agent/rag/{app_id}/query", body)

    async def get_approval(self, approval_id: str) -> dict:
        return await self._get(f"/v1/agent/approvals/{approval_id}")

    async def health(self) -> dict:
        r = await self._client.get(f"{self.url}/api/health")
        return r.json()

    # ---- operator surface (dashboard session) -----------------------------------
    async def admin_login(self):
        r = await self._client.post(f"{self.url}/v1/auth/login",
                                    json={"username": config.AOM_ADMIN_USERNAME, "password": config.AOM_ADMIN_PASSWORD})
        if r.status_code >= 400:
            raise AOMError(f"AOM admin login failed: HTTP {r.status_code} {r.text[:200]}", r.status_code)
        self._admin_cookies = r.cookies
        return r.json()

    async def admin(self, method: str, path: str, body=None):
        if self._admin_cookies is None:
            await self.admin_login()
        for attempt in range(2):
            r = await self._client.request(method, f"{self.url}{path}", json=body, cookies=self._admin_cookies,
                                           timeout=max(config.AOM_TIMEOUT_SECONDS, 120))
            if r.status_code == 401 and attempt == 0:
                await self.admin_login()
                continue
            if r.status_code >= 400:
                raise AOMError(f"AOM {method} {path} -> HTTP {r.status_code}: {r.text[:300]}", r.status_code, r.text)
            return r.json() if r.content else {}

    async def kb_ingest(self, title, content, metadata, source="databricks-daily-dump", filename=None) -> dict:
        return await self.admin("POST", "/v1/knowledge", {
            "title": title, "content": content, "encoding": "text", "filename": filename or f"{title}.md",
            "source": source, "allowed_roles": config.KB_ALLOWED_ROLES, "metadata": metadata,
        })

    async def kb_delete(self, document_id) -> None:
        try:
            await self.admin("DELETE", f"/v1/knowledge/{document_id}")
        except AOMError as exc:
            if exc.status != 404:
                raise


aom = AOMClient()
