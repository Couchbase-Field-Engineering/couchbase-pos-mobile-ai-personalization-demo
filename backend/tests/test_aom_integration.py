"""The AOM-governed path, against tests/fake_aom.py (same routes and shapes as
the real AOM): KB publishing, document-scoped retrieval, LLM cache across
shoppers, tool calls over real MCP, the human-approval tier, and the two store
agents (voice ordering, self-checkout vision) acting under their own identities.
"""
import os
import subprocess
import sys
import time
import uuid

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "backend")
AOM, API = "http://127.0.0.1:18090", "http://127.0.0.1:18201"


def wait(url, tries=80):
    for _ in range(tries):
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"{url} never came up")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    dump = tmp_path_factory.mktemp("dump")
    subprocess.run([sys.executable, os.path.join(ROOT, "databricks", "generate_mock_dump.py"), "--export-date",
                    "2026-11-13", "--target-mb", "2", "--out", str(dump)], check=True, capture_output=True)
    env = {**os.environ, "DEMO_HOUR": "9", "STORE_DRIVER": "memory", "DUMP_DIR": str(dump), "AOM_URL": AOM,
           "AOM_AGENT_API_KEY": "fake-agent-key", "AOM_ADMIN_PASSWORD": "x", "KB_PROFILE_LIMIT": "10",
           "SG_ADMIN_URL": "", "REQUEST_POLL_SECONDS": "0.3", "FAKE_MCP_URL": f"{API}/pos/mcp", "AOM_MCP_PUBLIC_URL": f"{API}/pos/mcp"}
    uv = [sys.executable, "-m", "uvicorn"]
    procs = [subprocess.Popen(uv + ["tests.fake_aom:app", "--port", "18090"], cwd=BACKEND, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    wait(f"{AOM}/api/health")
    procs.append(subprocess.Popen(uv + ["app.main:app", "--port", "18201"], cwd=BACKEND, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    wait(f"{API}/api/health")
    for _ in range(60):
        st = httpx.get(f"{API}/api/status").json()
        if (st["load"].get("kb") or {}).get("state") == "published":
            break
        time.sleep(0.25)
    yield
    for p in procs:
        p.terminate()


def login(member_number, last):
    r = httpx.post(f"{API}/api/auth/login", json={"member_number": member_number, "last_name": last})
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["shopper_id"]


def ask(h, q):
    return httpx.post(f"{API}/api/assistant", headers=h, json={"message": q}, timeout=30).json()


MAYA = "S00101001"
MIXER = "SS-660102"


def test_kb_published_and_scoped(stack):
    titles = list(httpx.get(f"{AOM}/test/state").json()["docs"].values())
    assert any(t.startswith("ShopSmart Shopping Guide") for t in titles)
    assert any(MAYA in t for t in titles)
    h, _ = login("204817", "Chen")
    a = ask(h, "What am I running low on?")
    assert a["context"]["kb_mode"] == "aom" and a["governed"]
    searched = [c[1] for c in httpx.get(f"{AOM}/test/state").json()["calls"] if c[0] == "kb_search"]
    assert all(d is not None for d in searched), "every KB search must be scoped to one document"


def test_llm_cache_shared_for_guide_only(stack):
    h1, _ = login("204817", "Chen")
    h2, _ = login("318552", "Walker")
    a = ask(h1, "How does a price match work?")
    b = ask(h2, "How does a price match work?")
    assert a["personal"] is False and b["llm"]["status"] == "hit_exact"
    p1 = ask(h1, "How many points do I have?")
    p2 = ask(h2, "How many points do I have?")
    assert p1["llm"]["namespace"] == "rag:shopsmart-pos-assistant:shopper" and p2["llm"]["status"] == "miss"


def test_rag_application_registered_and_scoped_to_guide(stack):
    st = httpx.get(f"{AOM}/test/state").json()
    app = st["rag_apps"]["shopsmart-pos-assistant"]
    guide = [d for d, t in st["docs"].items() if t.startswith("ShopSmart Shopping Guide")]
    assert app["enabled"] and app["document_ids"] == guide
    assert set(app["allowed_roles"]) == {"sales_agent", "admin"}

    h, _ = login("427190", "Alvarez")
    q = "What are the delivery options?"
    a, b = ask(h, q), ask(h, q)
    assert a["context"]["source"] == "aom-rag" and a["llm"]["rag_app"] == "shopsmart-pos-assistant"
    assert b["context"]["status"] == "hit" and b["llm"]["status"] == "hit_exact"
    assert "delivery" in a["reply"].lower() and a["sources"]
    rag = [c for c in httpx.get(f"{AOM}/test/state").json()["calls"] if c[0] == "rag_query"]
    # Guide answers never retrieve a shopper's profile.
    assert rag and all(set(c[3]) <= set(guide) for c in rag)


def test_shopper_context_cached_in_aom(stack):
    h, _ = login("318552", "Walker")
    q = "What did I order last time and how much have I spent?"
    first, second = ask(h, q), ask(h, q)
    assert second["context"]["status"] == "hit" and second["context"]["source"] == "aom"
    calls = httpx.get(f"{AOM}/test/state").json()["calls"]
    sets = [c for c in calls if c[0] == "ctx_set" and c[2].startswith("S00101002:history:")]
    assert sets and sets[0][1] == "rag:shopsmart-pos-assistant:shopper-context" and sets[0][3] is not None
    assert first["context"]["status"] in ("miss", "hit")


def test_price_match_waits_for_human(stack):
    h, sid = login("204817", "Chen")
    oid = f"order::{sid}::{uuid.uuid4().hex[:8]}"
    httpx.post(f"{API}/api/sync/push", headers=h, json={"docs": [{"collection": "orders", "id": oid, "doc": {
        "type": "request", "shopper_id": sid, "tool": "request_price_match",
        "args": {"lines": [{"sku": "SS-110245", "qty": 2}, {"sku": "SS-440129", "qty": 1}, {"sku": MIXER, "qty": 1}],
                 "sku": MIXER, "competitor": "Brightway Market", "competitor_price_usd": 239.99},
        "created_at": "2026-11-13T15:00:00Z"}}]})

    def req():
        docs = httpx.get(f"{API}/api/sync/pull", headers=h).json()["docs"]
        return next(d["doc"] for d in docs if d["id"] == oid)

    for _ in range(30):
        if req()["status"] == "awaiting_approval":
            break
        time.sleep(0.2)
    doc = req()
    assert doc["status"] == "awaiting_approval" and doc["governed"]
    httpx.post(f"{AOM}/test/approve/{doc['approval_id']}")
    for _ in range(30):
        if req()["status"] in ("confirmed", "partially_shipped"):
            break
        time.sleep(0.2)
    assert req()["status"] in ("confirmed", "partially_shipped")
    assert req()["item"]["price_match"]["matched_price_usd"] == 239.99


def _poll(url, done, tries=60):
    for _ in range(tries):
        d = httpx.get(url, timeout=10).json()
        if done(d):
            return d
        time.sleep(0.25)
    raise AssertionError(f"{url} never got there")


def test_voice_order_runs_as_the_voice_agent(stack):
    """A drive-thru order is a ticket the worker runs through AOM under the voice agent's own identity."""
    s = httpx.post(f"{API}/api/voice/session", json={"store_id": "ST-0202", "member_number": "318552"}).json()
    assert s["guest"]["first_name"] == "Devon"
    for text in ("yes", "no thanks, that's all", "yes"):
        s = httpx.post(f"{API}/api/voice/session/{s['session_id']}/turn", json={"text": text}, timeout=30).json()
    assert s["state"] == "placed"
    s = _poll(f"{API}/api/voice/session/{s['session_id']}", lambda d: d["ticket"]["status"] == "confirmed")
    assert s["ticket"]["governed"] and s["ticket"]["trace_id"] == "tr" and s["ticket"]["order_id"].startswith("PS-")
    by = httpx.get(f"{AOM}/test/state").json()["invoked_by"]
    assert ["shopsmart-pos-voice", "shopsmart-pos::place_pos_order"] in by


def test_checkout_exception_waits_for_the_attendant(stack):
    """A barcode that doesn't match what the camera and scale see pauses checkout until a person approves in AOM."""
    k = httpx.post(f"{API}/api/kiosk/session", json={"store_id": "ST-0101"}).json()
    url = f"{API}/api/kiosk/session/{k['session_id']}"
    k = httpx.post(f"{url}/event", json={"kind": "place", "data": {"sku": "SS-770128", "barcode": "SS-770114"}}).json()
    exc = k["exceptions"][0]
    assert exc["kind"] == "scan_mismatch" and exc["status"] == "open" and k["blockers"]
    k = httpx.post(f"{url}/exceptions/{exc['id']}/resolve", json={"decision": "charge_as_seen", "attendant": "Priya"}, timeout=30).json()
    held = k["exceptions"][0]
    assert held["status"] == "awaiting_approval" and held["approval_id"]
    assert k["items"][0]["sku"] == "SS-770114"                      # nothing changes until a person approves
    httpx.post(f"{AOM}/test/approve/{held['approval_id']}")
    k = _poll(url, lambda d: d["exceptions"][0]["status"] == "resolved")
    assert k["items"][0]["sku"] == "SS-770128" and k["exceptions"][0]["governed"] and not k["blockers"]
    k = httpx.post(f"{url}/event", json={"kind": "pay", "data": {}}, timeout=30).json()
    k = _poll(url, lambda d: d["state"] == "paid")
    assert k["ticket"]["governed"]
    by = httpx.get(f"{AOM}/test/state").json()["invoked_by"]
    assert ["shopsmart-pos-vision", "shopsmart-pos::resolve_checkout_exception"] in by
    assert ["shopsmart-pos-vision", "shopsmart-pos::place_pos_order"] in by
