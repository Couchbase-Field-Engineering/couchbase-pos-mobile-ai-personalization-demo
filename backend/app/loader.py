"""Daily Databricks export -> Couchbase + AOM Knowledge Base.

Run by the backend on startup and every DUMP_POLL_SECONDS (it only does work
when a newer `dt=YYYY-MM-DD/_SUCCESS` partition appears), or by hand:

    python -m app.loader [--dump-dir PATH] [--force] [--no-kb]

1. Stream every JSON Lines part file of the newest complete partition.
2. Upsert one `shoppers` document per shopper (all ~100MB in Couchbase
   mode). Live state that changed since the last export (orders placed,
   wallet, subscriptions, returns, preferences) is carried forward - in
   production that state would already be in the next Databricks export.
3. Index member numbers for sign-in, publish the catalog and one `stores`
   document per store, and build the recommendations for every recently
   active shopper (everyone else gets theirs at sign-in).
   The partition's `_pricing_signals.json` (competitor prices, demand
   forecast, promotion windows, bundles and cross-sell affinities) feeds the
   pricing engine, which writes one `pricing::<fc>` rule document per
   fulfillment center.
4. Publish to the AOM Knowledge Base: the Shopping Guide, and one profile
   document per recently active shopper (demo personas first), replacing
   yesterday's version of each. The assistant's AOM RAG Application is then
   re-scoped to the new guide document.

Context caches need no sweep: every cached context records the export date it
was built from, so the new export makes them all stale at once.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import glob
import json
import logging
import os
import time

from . import catalog as C
from . import config, knowledge, personalization, pricing, rag_app
from .aom_client import AOMError, aom
from .docs import (CATALOG_CHANNEL, CATALOG_ID, STATE, now_iso, shopper_channel, shopper_id_doc, store_channel,
                   store_id_doc)
from .store import store

log = logging.getLogger("pos.loader")
PERSONAS = ["S00101001", "S00101002", "S00101003"]   # demo shoppers (member numbers 204817, 318552, 427190)
LIVE_FIELDS = ("order_history", "replenishment", "subscriptions", "returns", "credits", "spend", "wallet", "preferences")
STATUS = {"state": "idle"}
_TASKS: list = []  # keep a reference so background KB publishing is not garbage-collected


def latest_partition(dump_dir: str) -> str | None:
    parts = sorted(p for p in glob.glob(os.path.join(dump_dir, "dt=*")) if os.path.exists(os.path.join(p, "_SUCCESS")))
    return parts[-1] if parts else None


def read_signals(partition: str) -> dict | None:
    path = os.path.join(partition, "_pricing_signals.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


async def load_pricing(partition: str | None, why: str):
    """Pricing signals from the export -> the engine's rule documents. The
    demo clock (a presenter's override on the merchandising console) survives
    a reload."""
    prev = await store.get("lookups", "pricing_signals") or {}
    # An export without a signals file keeps yesterday's signals rather than dropping to the built-in defaults.
    sig = (read_signals(partition) if partition else None) or ({**prev} if prev.get("bundles") else None) or pricing.default_signals()
    sig["clock"] = prev.get("clock") or {"tz": config.PRICING_TZ, "demo_hour": config.DEMO_HOUR}
    await pricing.set_signals(sig)
    await pricing.refresh_all(why)
    return sig


def read_rows(partition: str):
    for path in sorted(glob.glob(os.path.join(partition, "part-*.json"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


async def mark_live(shopper_id: str):
    """Called after an action - remembers which shoppers have state newer than the export."""
    doc = await store.get("lookups", "live_shoppers") or {"shoppers": []}
    if shopper_id not in doc["shoppers"]:
        doc["shoppers"].append(shopper_id)
        await store.upsert("lookups", "live_shoppers", doc)


async def load(dump_dir: str | None = None, force=False, kb=True) -> dict:
    dump_dir = os.path.abspath(dump_dir or config.DUMP_DIR)
    part = latest_partition(dump_dir)
    if not part:
        log.warning("No complete Databricks partition (dt=*/_SUCCESS) under %s", dump_dir)
        STATUS.update(state="no_dump", dump_dir=dump_dir)
        return STATUS
    export_date = os.path.basename(part).split("=", 1)[1]
    state = await store.get("lookups", "load_state") or {}
    if state.get("export_date") == export_date and not force and state.get("driver") == store.driver and store.driver == "couchbase":
        STATE["export_date"] = export_date
        STATUS.update(state="loaded", **state)
        log.info("Export %s already loaded", export_date)
        if not await store.get("catalog", pricing.pricing_id("FC-NE")):
            await load_pricing(part, "startup")
        pb = await store.get("lookups", "kbdoc::guide") or {}
        if kb and config.KB_ENABLED and aom.admin_enabled and pb.get("export_date") != export_date:
            _TASKS.append(asyncio.create_task(publish_kb(export_date, await _kb_window())))
        elif kb and aom.admin_enabled and pb.get("document_id"):
            _TASKS.append(asyncio.create_task(rag_app.sync(pb["document_id"])))
        return STATUS

    t0 = time.time()
    STATE["export_date"] = export_date
    STATUS.update(state="loading", export_date=export_date, partition=part, started_at=now_iso())
    live = set((await store.get("lookups", "live_shoppers") or {}).get("shoppers", []))
    today = dt.date.fromisoformat(config.DEMO_TODAY or export_date)
    recs_since = (today - dt.timedelta(days=config.RECS_WINDOW_DAYS)).isoformat()
    batch: dict[str, dict] = {}
    logins: dict[str, dict] = {}
    stores_seen: set[str] = set()
    rows = skipped = carried = 0
    build: list[str] = []
    nbytes = sum(os.path.getsize(p) for p in glob.glob(os.path.join(part, "part-*.json")))

    for row in read_rows(part):
        sid = row["shopper_id"]
        if store.driver == "memory" and rows >= config.MEMORY_SHOPPER_LIMIT and sid not in PERSONAS:
            skipped += 1
            continue
        doc = {**row, "type": "shopper", "rev": 0, "channels": [shopper_channel(sid)], "loaded_at": now_iso()}
        if sid in live:
            prev = await store.get("shoppers", shopper_id_doc(sid))
            if prev:
                for f in LIVE_FIELDS:
                    if f in prev:
                        doc[f] = prev[f]
                if prev.get("delivery", {}).get("default_option"):
                    doc["delivery"]["default_option"] = prev["delivery"]["default_option"]
                doc["rev"] = int(prev.get("rev", 0)) + 1
                carried += 1
        batch[shopper_id_doc(sid)] = doc
        logins[f"login::{row['member_number']}"] = {"type": "login_index", "shopper_id": sid, "last_name": row["last_name"].lower(),
                                                    "store_id": (row.get("home_store") or {}).get("store_id")}
        stores_seen.add((row.get("home_store") or {}).get("store_id"))
        if sid in PERSONAS or (row.get("engagement", {}).get("last_app_open") or "") >= recs_since:
            build.append(sid)
        rows += 1
        if len(batch) >= 1000:
            await store.upsert_many("shoppers", batch)
            await store.upsert_many("lookups", logins)
            batch, logins = {}, {}
            STATUS["rows"] = rows
    await store.upsert_many("shoppers", batch)
    await store.upsert_many("lookups", logins)

    signals = await load_pricing(part, f"export {export_date}")
    cat = C.public_catalog()
    cat.update({"pairs_with": signals.get("pairs_with") or cat["pairs_with"], "upgrades": signals.get("upgrades") or cat["upgrades"]})
    cat.update({"type": "catalog", "faq": [{"q": f["q"], "a": f["a"], "keywords": f["keywords"]} for f in knowledge.FAQ],
                "export_date": export_date, "channels": [CATALOG_CHANNEL], "updated_at": now_iso()})
    await store.upsert("catalog", CATALOG_ID, cat)

    # One document per store. A shopper's phone only replicates their home store's channel.
    await store.upsert_many("stores", {
        store_id_doc(sid): {"type": "store", **C.store_doc_body(sid), "export_date": export_date,
                            "channels": [store_channel(sid)], "updated_at": now_iso()}
        for sid in C.STORES})

    for sid in build:
        await personalization.refresh_recommendations(sid)

    summary = {"export_date": export_date, "partition": part, "rows": rows, "stores": len(C.STORES),
               "home_stores_in_export": len(stores_seen - {None}), "skipped_over_memory_limit": skipped,
               "carried_live_state": carried, "bytes": nbytes, "recommendations_built": len(build), "pricing_signals": signals.get("source"),
               "load_seconds": round(time.time() - t0, 1), "driver": store.driver, "loaded_at": now_iso()}
    await store.upsert("lookups", "load_state", summary)
    STATUS.update(state="loaded", **summary)
    log.info("Loaded export %s: %s shoppers (%s recommendation sets) in %.1fs", export_date, rows, len(build), time.time() - t0)

    if kb and config.KB_ENABLED and aom.admin_enabled:
        _TASKS.append(asyncio.create_task(publish_kb(export_date, await _kb_window())))
    elif kb and config.KB_ENABLED:
        STATUS["kb"] = {"state": "skipped", "reason": "AOM_URL / AOM_ADMIN_PASSWORD not set"}
    return STATUS


async def _replace_kb_doc(key: str, title: str, content: str, metadata: dict):
    prev = await store.get("lookups", f"kbdoc::{key}")
    res = await aom.kb_ingest(title, content, metadata)
    new_id = res["document"]["document_id"]
    await store.upsert("lookups", f"kbdoc::{key}", {"type": "kb_index", "document_id": new_id, "title": title,
                                                    "export_date": metadata.get("export_date"), "at": now_iso()})
    if prev and prev.get("document_id") and prev["document_id"] != new_id:
        await aom.kb_delete(prev["document_id"])
    return new_id


async def publish_kb(export_date: str, shopper_ids: list[str]):
    t0 = time.time()
    STATUS["kb"] = {"state": "publishing", "total": len(shopper_ids) + 1, "done": 0, "errors": 0}
    try:
        guide_id = await _replace_kb_doc("guide", f"ShopSmart Shopping Guide {export_date}",
                                         knowledge.guide_markdown(export_date),
                                         {"type": "shopping_guide", "export_date": export_date, "pii": False})
        STATUS["kb"]["done"] += 1
        await rag_app.sync(guide_id)
    except AOMError as exc:
        log.error("KB guide publish failed: %s", exc)
        STATUS["kb"].update(state="error", error=str(exc)[:300])
        return
    sem = asyncio.Semaphore(4)

    async def one(sid):
        async with sem:
            s = await store.get("shoppers", shopper_id_doc(sid))
            if not s:
                return
            try:
                await _replace_kb_doc(sid, f"Shopper profile {sid} {export_date}", knowledge.shopper_profile_markdown(s),
                                      {"type": "shopper_profile", "shopper_id": sid, "export_date": export_date, "pii": True})
                STATUS["kb"]["done"] += 1
            except AOMError as exc:
                STATUS["kb"]["errors"] += 1
                log.warning("KB publish for %s failed: %s", sid, exc)

    await asyncio.gather(*(one(s) for s in shopper_ids))
    STATUS["kb"].update(state="published", seconds=round(time.time() - t0, 1), export_date=export_date)
    log.info("Published %d KB documents to AOM in %.1fs", STATUS["kb"]["done"], time.time() - t0)


async def poll_forever():
    while True:
        await asyncio.sleep(config.DUMP_POLL_SECONDS)
        try:
            part = latest_partition(os.path.abspath(config.DUMP_DIR))
            if part and os.path.basename(part).split("=", 1)[1] != STATE.get("export_date"):
                log.info("New Databricks partition detected: %s", part)
                await load()
        except Exception as exc:  # noqa: BLE001
            log.warning("dump poll failed: %s", exc)


async def _kb_window() -> list[str]:
    """KB targets: the demo personas first, then the shoppers who opened the
    app most recently (within KB_WINDOW_DAYS), up to KB_PROFILE_LIMIT."""
    today = dt.date.fromisoformat(config.DEMO_TODAY or STATE["export_date"])
    since = (today - dt.timedelta(days=config.KB_WINDOW_DAYS)).isoformat()
    out = [sid for sid in PERSONAS if await store.get("shoppers", shopper_id_doc(sid))]
    out += [sid for sid in await store.recently_active(since, config.KB_PROFILE_LIMIT) if sid not in out]
    return out[: max(config.KB_PROFILE_LIMIT, len(PERSONAS))]


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-dir")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-kb", action="store_true")
    args = ap.parse_args()

    async def run():
        await store.connect()
        await load(args.dump_dir, force=args.force, kb=False)
        if not args.no_kb and config.KB_ENABLED and aom.admin_enabled:
            state = await store.get("lookups", "load_state")
            await publish_kb(state["export_date"], await _kb_window())
        print(json.dumps({k: v for k, v in STATUS.items()}, indent=2, default=str))

    asyncio.run(run())


if __name__ == "__main__":
    main()
