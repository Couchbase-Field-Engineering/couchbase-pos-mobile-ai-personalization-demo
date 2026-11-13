"""Self-registration (AOM_AUTO_REGISTER, used by the Helm chart) against a
*fresh* fake AOM: the backend sets the admin password, registers its MCP
server (AOM ingests it over real MCP), issues its agent identity, turns on
the approval tier, publishes the KB and the RAG application - then answers
governed assistant questions with no register script and no agent key given.
"""
import os
import subprocess
import sys
import time

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "backend")
AOM, API = "http://127.0.0.1:18092", "http://127.0.0.1:18203"


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
    env = {**os.environ, "DEMO_HOUR": "9", "STORE_DRIVER": "memory", "DUMP_DIR": str(dump), "AOM_URL": AOM, "AOM_AGENT_API_KEY": "",
           "AOM_ADMIN_PASSWORD": "Retail-Demo-2026", "AOM_AUTO_REGISTER": "true", "KB_PROFILE_LIMIT": "10",
           "AOM_MCP_PUBLIC_URL": f"{API}/pos/mcp", "SG_ADMIN_URL": "", "REQUEST_POLL_SECONDS": "0.3",
           "FAKE_MCP_URL": f"{API}/pos/mcp", "FAKE_FRESH_AOM": "1"}
    uv = [sys.executable, "-m", "uvicorn"]
    procs = [subprocess.Popen(uv + ["tests.fake_aom:app", "--port", "18092"], cwd=BACKEND, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    wait(f"{AOM}/api/health")
    procs.append(subprocess.Popen(uv + ["app.main:app", "--port", "18203"], cwd=BACKEND, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    wait(f"{API}/api/health")
    for _ in range(120):
        st = httpx.get(f"{API}/api/status").json()
        if (st["load"].get("kb") or {}).get("state") == "published":
            break
        time.sleep(0.25)
    yield st
    for p in procs:
        p.terminate()


def test_registers_itself_with_a_fresh_aom(stack):
    st = httpx.get(f"{API}/api/status").json()
    assert st["aom"]["registration"]["state"] == "registered" and st["aom"]["configured"]
    fake = httpx.get(f"{AOM}/test/state").json()
    assert fake["admin_pw_set"] and fake["servers"] == ["shopsmart-pos"]
    assert [a["name"] for a in fake["agents"]] == ["ShopSmart POS Assistant", "ShopSmart POS Voice Ordering Agent",
                                                    "ShopSmart POS Self-Checkout Vision Agent"]
    assert [len(a["allowed_tools"]) for a in fake["agents"]] == [10, 4, 3]   # each agent is scoped to its own job
    assert fake["approvals_cfg"]["require_for_tools"] == ["shopsmart-pos::issue_goodwill_credit",
                                                          "shopsmart-pos::request_price_match",
                                                          "shopsmart-pos::resolve_checkout_exception"]
    assert fake["rag_apps"]["shopsmart-pos-assistant"]["enabled"]


def test_assistant_is_governed_after_self_registration(stack):
    r = httpx.post(f"{API}/api/auth/login", json={"member_number": "204817", "last_name": "Chen"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    a = httpx.post(f"{API}/api/assistant", headers=h, json={"message": "What am I running low on?"}, timeout=30).json()
    assert a["governed"] and a["context"]["kb_mode"] == "aom"
    b = httpx.post(f"{API}/api/assistant", headers=h, json={"message": "How does a price match work?"}, timeout=30).json()
    assert b["context"]["source"] == "aom-rag"
