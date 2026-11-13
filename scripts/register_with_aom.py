#!/usr/bin/env python3
"""Register this demo with the Couchbase Agent Operations Manager (AOM), by hand.

Does for Docker Compose what app/aom_bootstrap.py does automatically in the
Helm chart (AOM_AUTO_REGISTER):

1. signs in to AOM as the dashboard admin
2. registers the `shopsmart-pos` MCP server (this backend, at AOM_MCP_PUBLIC_URL)
   so AOM ingests its 13 tools into the vetted catalog
3. issues three agent identities (backend/app/agents.py), each scoped to its
   own tools, and writes their API keys to .env: the guest app's assistant,
   the voice ordering agent and the self-checkout vision agent
4. puts price matches, goodwill credits and self-checkout exceptions behind
   AOM's human-approval tier
5. registers the `shopsmart-pos-assistant` RAG Application (scoped to the
   Shopping Guide once the backend has published it)

Idempotent. `--rotate` issues new agent keys.

    python3 scripts/register_with_aom.py --admin-password '<AOM admin password>'
    docker compose up -d --build backend      # picks up AOM_* from .env
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app import config  # noqa: E402
from app.actions import APPROVAL_TOOLS  # noqa: E402
from app.agents import AGENTS, OWNER  # noqa: E402
from app.rag_app import definition  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aom-url", default=os.getenv("AOM_URL_LOCAL", "https://localhost:8090"),
                    help="AOM as reached from this machine (default https://localhost:8090)")
    ap.add_argument("--admin-username", default=os.getenv("AOM_ADMIN_USERNAME", "admin"))
    ap.add_argument("--admin-password", default=os.getenv("AOM_ADMIN_PASSWORD"), required=not os.getenv("AOM_ADMIN_PASSWORD"))
    ap.add_argument("--mcp-url", default=config.AOM_MCP_PUBLIC_URL, help="this backend's MCP server as AOM reaches it")
    ap.add_argument("--env-file", default=os.path.join(ROOT, ".env"))
    ap.add_argument("--rotate", action="store_true", help="issue a new agent key even if one exists")
    ap.add_argument("--verify-tls", action="store_true")
    args = ap.parse_args()

    c = httpx.Client(base_url=args.aom_url.rstrip("/"), verify=args.verify_tls, timeout=30)
    r = c.post("/v1/auth/login", json={"username": args.admin_username, "password": args.admin_password})
    if r.status_code == 409:
        print("Fresh AOM: setting the admin password through first-login setup")
        r = c.post("/v1/auth/bootstrap", json={"password": args.admin_password})
    r.raise_for_status()
    print(f"Signed in to AOM at {args.aom_url}")

    server = config.AOM_MCP_SERVER_ID
    r = c.post("/v1/servers", json={"server_id": server, "label": "ShopSmart POS", "owner": OWNER,
                                    "mcp_url": args.mcp_url, "trust_status": "trusted",
                                    "default_allowed_roles": ["sales_agent", "admin"]})
    if r.status_code == 409:
        r = c.post(f"/v1/servers/{server}/reingest")
    r.raise_for_status()
    body = r.json()
    if body.get("ingest_error"):
        sys.exit(f"AOM could not reach the MCP server at {args.mcp_url}: {body['ingest_error']} (is the backend up?)")
    print(f"MCP server {server}: {body.get('ingested_tools')} tools ingested from {args.mcp_url}")

    agents = c.get("/v1/agents").raise_for_status().json().get("agents", [])
    env_now = open(args.env_file).read() if os.path.exists(args.env_file) else ""
    keys = {}
    for spec in AGENTS.values():
        tools = [f"{server}::{t}" for t in spec["tools"]]
        existing = next((a for a in agents if a.get("name") == spec["name"] and a.get("status") == "active"), None)
        m = re.search(rf"^{spec['env']}=(.+)$", env_now, re.M)
        api_key = m.group(1).strip() if m else None
        if existing and not args.rotate and api_key:
            print(f"Agent {spec['name']} exists ({existing['agent_id']}); keeping the key in .env (use --rotate for a new one)")
        elif existing:
            r = c.post(f"/v1/agents/{existing['agent_id']}/rotate", json={"revoke_immediately": True}).raise_for_status()
            api_key = r.json()["api_key"]
            print(f"Rotated the key of agent {existing['agent_id']} ({spec['name']})")
        else:
            r = c.post("/v1/agents", json={"name": spec["name"], "role": spec["role"], "owner": OWNER,
                                           "description": spec["description"], "allowed_tools": tools}).raise_for_status()
            api_key = r.json()["api_key"]
            print(f"Issued agent identity {r.json()['agent']['agent_id']} ({spec['name']}, {len(tools)} tools)")
        keys[spec["env"]] = api_key

    cfg = c.get("/v1/approvals-config").raise_for_status().json()["config"]
    want = {f"{server}::{t}" for t in APPROVAL_TOOLS}
    cfg["enabled"] = True
    cfg["require_for_tools"] = sorted(set(cfg.get("require_for_tools") or []) | want)
    c.put("/v1/approvals-config", json={"config": cfg}).raise_for_status()
    print(f"Approval tier on for: {', '.join(sorted(want))}")

    app = definition(None)
    r = c.post("/v1/rag/apps", json={"app": app, "issue_key": False})
    if r.status_code == 409:
        print(f"RAG application {app['app_id']} already registered; the backend re-scopes it on each load")
    elif r.status_code in (404, 405):
        print("This AOM build has no RAG Applications page; skipping")
    else:
        r.raise_for_status()
        print(f"RAG application {app['app_id']} registered (disabled until the backend publishes the Shopping Guide)")

    env = open(args.env_file).read() if os.path.exists(args.env_file) else open(os.path.join(ROOT, ".env.example")).read()
    values = {"AOM_URL": os.getenv("AOM_URL", "https://host.docker.internal:8090"), **keys,
              "AOM_ADMIN_USERNAME": args.admin_username, "AOM_ADMIN_PASSWORD": args.admin_password}
    for k, v in values.items():
        if re.search(rf"^{k}=.*$", env, re.M):
            env = re.sub(rf"^{k}=.*$", f"{k}={v}", env, flags=re.M)
        else:
            env += f"\n{k}={v}\n"
    with open(args.env_file, "w") as fh:
        fh.write(env)
    print(f"Wrote AOM_* to {args.env_file}. Now: docker compose up -d --build backend")


if __name__ == "__main__":
    main()
