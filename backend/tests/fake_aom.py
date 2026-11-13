"""A small stand-in for the Couchbase Agent Operations Manager, used only by
tests/test_aom_integration.py. It mirrors the request/response shapes of the
AOM routes this demo calls (auth, knowledge, memory, LLM complete with a
cache, context cache, RAG applications, tools/invoke over real MCP, approvals) - not their behavior in depth.

    uvicorn tests.fake_aom:app --port 8090
"""
import json
import os
import uuid

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

app = FastAPI()
S = {"docs": {}, "memory": [], "cache": {}, "approvals": {}, "calls": [], "servers": {}, "ctx": {}, "rag_apps": {}}
KEY = "fake-agent-key"
APPROVAL_TOOLS = {"shopsmart-pos::request_price_match", "shopsmart-pos::issue_goodwill_credit",
                  "shopsmart-pos::resolve_checkout_exception"}


def auth(h):
    if h != f"Bearer {KEY}":
        raise HTTPException(401, "bad key")


@app.get("/api/health")
def health():
    return {"status": "ok", "fake": True}


# FAKE_FRESH_AOM=1: like a new AOM install, the admin account has no password
# until POST /v1/auth/bootstrap sets one. Otherwise any login succeeds.
S["admin_pw"] = None
S["agents"] = {}
S["approvals_cfg"] = {"enabled": False, "require_for_tools": []}
FRESH = os.getenv("FAKE_FRESH_AOM") == "1"


@app.post("/v1/auth/login")
def login(body: dict, response: Response):
    if FRESH and not S["admin_pw"]:
        # What the real AOM answers before the first-login password is set.
        raise HTTPException(409, "This account has no password set yet - use the setup form instead.")
    if FRESH and body.get("password") != S["admin_pw"]:
        raise HTTPException(401, "bad credentials")
    response.set_cookie("aom_session", "admin")
    return {"user": {"username": body["username"], "role": "admin"}}


@app.post("/v1/auth/bootstrap")
def bootstrap(body: dict, response: Response):
    if S["admin_pw"]:
        raise HTTPException(409, "already set")
    S["admin_pw"] = body["password"]
    response.set_cookie("aom_session", "admin")
    return {"user": {"username": "admin"}}


def admin_only(request: Request):
    if request.cookies.get("aom_session") != "admin":
        raise HTTPException(401, "dashboard session required")


@app.post("/v1/servers")
async def server_create(body: dict, request: Request):
    admin_only(request)
    if body["server_id"] in S["servers"]:
        raise HTTPException(409, "exists")
    S["servers"][body["server_id"]] = body
    return {"ingested_tools": len(await list_mcp_tools(body["mcp_url"]))}


@app.post("/v1/servers/{sid}/reingest")
async def server_reingest(sid: str, request: Request):
    admin_only(request)
    return {"ingested_tools": len(await list_mcp_tools(S["servers"][sid]["mcp_url"]))}


@app.get("/v1/agents")
def agents_list(request: Request):
    admin_only(request)
    return {"agents": list(S["agents"].values())}


@app.post("/v1/agents")
def agent_create(body: dict, request: Request):
    admin_only(request)
    aid = f"agent-{len(S['agents']) + 1}"
    S["agents"][aid] = {"agent_id": aid, "name": body["name"], "status": "active", "allowed_tools": body["allowed_tools"]}
    return {"agent": S["agents"][aid], "api_key": KEY}


@app.post("/v1/agents/{aid}/rotate")
def agent_rotate(aid: str, request: Request):
    admin_only(request)
    return {"agent": S["agents"][aid], "api_key": KEY}


@app.get("/v1/approvals-config")
def approvals_get(request: Request):
    admin_only(request)
    return {"config": S["approvals_cfg"]}


@app.put("/v1/approvals-config")
def approvals_put(body: dict, request: Request):
    admin_only(request)
    S["approvals_cfg"] = body["config"]
    return {"config": S["approvals_cfg"]}


@app.post("/v1/tools/discover")
def discover(body: dict, authorization: str = Header(None)):
    auth(authorization)
    return {"tools": []}


@app.post("/v1/knowledge")
def ingest(body: dict, request: Request):
    assert request.cookies.get("aom_session") == "admin"
    doc_id = f"kb-{uuid.uuid4().hex[:8]}"
    content = body["content"]
    chunks = [content[i:i + 1200] for i in range(0, len(content), 1000)]
    S["docs"][doc_id] = {"title": body["title"], "chunks": chunks, "roles": body["allowed_roles"]}
    S["calls"].append(("kb_ingest", body["title"]))
    return {"document": {"document_id": doc_id, "title": body["title"], "chunk_count": len(chunks)}}


@app.delete("/v1/knowledge/{doc_id}")
def delete(doc_id: str):
    S["docs"].pop(doc_id, None)
    return {"deleted": True}


@app.post("/v1/agent/knowledge/search")
def kb_search(body: dict, authorization: str = Header(None)):
    auth(authorization)
    words = set(body["query"].lower().split())
    out = []
    for did, d in S["docs"].items():
        if body.get("document_id") and did != body["document_id"]:
            continue
        for c in d["chunks"]:
            score = sum(1 for w in words if w in c.lower()) / (len(words) or 1)
            out.append({"document_id": did, "document_title": d["title"], "content": c, "score": score})
    out.sort(key=lambda r: -r["score"])
    S["calls"].append(("kb_search", body.get("document_id")))
    return {"role": "sales_agent", "results": out[: body.get("top_k", 5)], "latency_ms": 3, "trace": {"trace_id": "t"}}


@app.post("/v1/memory")
def mem_add(body: dict, authorization: str = Header(None)):
    auth(authorization)
    S["memory"].append(body)
    return {"memory_id": f"m{len(S['memory'])}", "user_id": body["user_id"]}


@app.post("/v1/memory/search")
def mem_search(body: dict, authorization: str = Header(None)):
    auth(authorization)
    entries = [{"content": m["content"]} for m in S["memory"] if m["user_id"] == body["user_id"]]
    return {"user_id": body["user_id"], "entries": entries[-body.get("top_k", 5):]}


@app.post("/v1/llm/complete")
def complete(body: dict, authorization: str = Header(None), x_aom_trace_id: str = Header(None)):
    auth(authorization)
    S["calls"].append(("llm", body["namespace"], x_aom_trace_id))
    key = (body["namespace"], body["prompt"])
    if key in S["cache"]:
        return {"response": S["cache"][key], "provider": "stub", "model": "stub", "stub": True,
                "cache": {"status": "hit_exact", "similarity": 1.0}, "tokens_saved": 120}
    # Like AOM with no provider key configured: a labelled stub, so the
    # backend composes the answer from the retrieved context itself.
    text = f"[stub completion #{len(S['cache']) + 1}]"
    S["cache"][key] = text
    return {"response": text, "provider": "stub", "model": "stub", "cache": {"status": "miss"}, "stub": True}


@app.post("/v1/context/get")
def ctx_get(body: dict, authorization: str = Header(None)):
    auth(authorization)
    k = (body.get("namespace"), body["key"])
    hit = k in S["ctx"]
    S["calls"].append(("ctx_get", body.get("namespace"), body["key"], hit))
    return {"hit": hit, "value": S["ctx"].get(k), "cache": {"status": "hit" if hit else "miss"}, "latency_ms": 1}


@app.post("/v1/context/set")
def ctx_set(body: dict, authorization: str = Header(None)):
    auth(authorization)
    S["ctx"][(body.get("namespace"), body["key"])] = body["value"]
    S["calls"].append(("ctx_set", body.get("namespace"), body["key"], body.get("source_latency_ms")))
    return {"stored": True}


@app.get("/v1/rag/apps")
def rag_list():
    return {"apps": list(S["rag_apps"].values())}


@app.post("/v1/rag/apps")
def rag_create(body: dict, request: Request):
    assert request.cookies.get("aom_session") == "admin"
    a = body["app"]
    if a["app_id"] in S["rag_apps"]:
        raise HTTPException(409, "exists")
    S["rag_apps"][a["app_id"]] = a
    S["calls"].append(("rag_app_create", a["app_id"], tuple(a["document_ids"])))
    return {"app": a, "api_key": None}


@app.put("/v1/rag/apps/{app_id}")
def rag_update(app_id: str, body: dict, request: Request):
    assert request.cookies.get("aom_session") == "admin"
    if app_id not in S["rag_apps"]:
        raise HTTPException(404)
    S["rag_apps"][app_id].update(body["app"])
    S["calls"].append(("rag_app_update", app_id, tuple(body["app"]["document_ids"])))
    return {"app": S["rag_apps"][app_id]}


@app.post("/v1/agent/rag/{app_id}/query")
def rag_query(app_id: str, body: dict, authorization: str = Header(None)):
    auth(authorization)
    a = S["rag_apps"].get(app_id)
    if not a:
        raise HTTPException(404, "no such app")
    if not a.get("enabled"):
        raise HTTPException(403, "disabled")
    q = body["question"].strip().lower().rstrip("?.! ")
    ck = ("rag:" + app_id, q)
    hit = ck in S["ctx"]
    if not hit:
        res = kb_search({"query": q, "top_k": a["top_k"], "document_id": a["document_ids"][0]}, f"Bearer {KEY}")
        S["ctx"][ck] = res["results"]
    chunks = S["ctx"][ck]
    llm = complete({"prompt": a["system_prompt"] + q, "namespace": f"rag:{app_id}:fp"}, f"Bearer {KEY}", None)
    S["calls"].append(("rag_query", app_id, "hit" if hit else "miss", tuple(sorted({c["document_id"] for c in chunks}))))
    return {"app_id": app_id, "answer": llm["response"],
            "sources": [{"n": i, "document_id": c["document_id"], "document_title": c["document_title"],
                         "preview": c["content"][:240], "score": c["score"]} for i, c in enumerate(chunks, 1)],
            "retrieval": {"cache": "hit" if hit else "miss", "chunks": len(chunks), "latency_ms": 2},
            "llm": {"cache": llm["cache"], "provider": "stub", "model": "stub", "stub": True},
            "trace": {"trace_id": "tr-rag"}}


async def list_mcp_tools(url):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return (await s.list_tools()).tools


async def call_mcp(url, tool, args):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            return json.loads(res.content[0].text)


@app.post("/v1/tools/invoke")
async def invoke(body: dict, authorization: str = Header(None), x_aom_agent_id: str = Header(None)):
    auth(authorization)
    tid = body["tool_id"]
    S["calls"].append(("invoke", tid))
    S.setdefault("invoked_by", []).append([x_aom_agent_id, tid])
    if tid in APPROVAL_TOOLS:
        aid = body.get("approval_id")
        if not aid:
            aid = f"ap-{uuid.uuid4().hex[:8]}"
            S["approvals"][aid] = {"approval_id": aid, "status": "pending", "tool_id": tid}
            return JSONResponse(status_code=202, content={"status": "pending_approval",
                                                          "approval": S["approvals"][aid], "trace": {"trace_id": "tr-ap"}})
        if S["approvals"].get(aid, {}).get("status") != "approved":
            raise HTTPException(403, "not approved")
        S["approvals"][aid]["status"] = "consumed"
    server, tool = tid.split("::")
    result = await call_mcp(os.getenv("FAKE_MCP_URL", "http://127.0.0.1:8204/pos/mcp"), tool, body["arguments"])
    return {"role": "sales_agent", "tool_id": tid, "result": result, "latency_ms": 5, "trace": {"trace_id": "tr"}}


@app.get("/v1/agent/approvals/{aid}")
def approval(aid: str, authorization: str = Header(None)):
    auth(authorization)
    if aid not in S["approvals"]:
        raise HTTPException(404)
    return {"approval": S["approvals"][aid]}


@app.post("/test/approve/{aid}")
def approve(aid: str):
    S["approvals"][aid]["status"] = "approved"
    return S["approvals"][aid]


@app.get("/test/state")
def state():
    return {"docs": {k: v["title"] for k, v in S["docs"].items()}, "calls": S["calls"][-300:],
            "rag_apps": S["rag_apps"], "ctx_keys": [list(k) for k in S["ctx"]],
            "admin_pw_set": bool(S["admin_pw"]), "servers": list(S["servers"]), "agents": list(S["agents"].values()),
            "approvals_cfg": S["approvals_cfg"],
            "approvals": S["approvals"], "memory": len(S["memory"]), "invoked_by": S.get("invoked_by", [])}
