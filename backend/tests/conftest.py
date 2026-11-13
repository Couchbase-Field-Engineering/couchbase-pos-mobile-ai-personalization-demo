"""One in-memory backend for the whole test session (no Couchbase, no AOM).

The pricing clock is pinned to 9 am, so the hour-based promotions are the
same whenever the tests run.
"""
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPORT = "2026-11-13"


@pytest.fixture(scope="session")
def dump(tmp_path_factory):
    d = tmp_path_factory.mktemp("dump")
    subprocess.run([sys.executable, os.path.join(ROOT, "databricks", "generate_mock_dump.py"),
                    "--export-date", EXPORT, "--target-mb", "3", "--out", str(d)], check=True, capture_output=True)
    return d


@pytest.fixture(scope="session")
def rows(dump):
    """The export's shopper rows, for tests that need a shopper nobody else has touched."""
    part = os.path.join(dump, f"dt={EXPORT}")
    out = []
    for name in sorted(os.listdir(part)):
        if name.startswith("part-"):
            with open(os.path.join(part, name)) as fh:
                out += [json.loads(line) for line in fh]
    return out


@pytest.fixture(scope="session")
def client(dump):
    os.environ.update(STORE_DRIVER="memory", DUMP_DIR=str(dump), AOM_URL="", SG_ADMIN_URL="", DEMO_HOUR="9")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        for _ in range(100):
            if c.get("/api/health").json()["load"] == "loaded":
                break
            time.sleep(0.1)
        yield c
