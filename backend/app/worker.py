"""Processes offline-first orders and other shopper actions.

The phone never calls an ordering API directly. A tap writes an `orders`
document into Couchbase Lite (`status: "pending"`); the replicator pushes it
through Sync Gateway whenever the shopper has connectivity, and this worker picks
it up from Couchbase, runs it through AOM (tool_gateway), and writes the
outcome back onto the same document. Sync Gateway replicates the update to
the phone, where a live query flips the card from "On device" to "Confirmed".
Price matches and goodwill credits sit in `awaiting_approval` until a
person decides in AOM's Approvals page; this worker polls and re-invokes with
the approval id.
"""
from __future__ import annotations

import asyncio
import logging

from . import config, metrics, tool_gateway
from .actions import READ_ONLY_TOOLS, STORE_ONLY_TOOLS, TOOLS
from .docs import now_iso, shopper_channel, store_channel
from .store import store

log = logging.getLogger("pos.worker")

# Tools a shopper's phone may request. Read-only tools are answered by the
# assistant; nothing here lets a phone act as another shopper.
REQUESTABLE = set(TOOLS) - set(READ_ONLY_TOOLS)


async def process(key: str, doc: dict) -> dict:
    first = doc.get("status") != "awaiting_approval"
    shopper_id = doc.get("shopper_id")
    tool = doc.get("tool")
    if doc.get("status") == "awaiting_approval":
        status = await tool_gateway.approval_status(doc.get("approval_id", ""))
        if status in (None, "pending"):
            return doc
        if status != "approved":
            doc.update(status="failed", summary=f"Customer Care did not approve this request ({status}).",
                       completed_at=now_iso())
            await store.upsert("orders", key, doc)
            return doc
        result = await tool_gateway.invoke(tool, {**doc.get("args", {}), "shopper_id": shopper_id},
                                           approval_id=doc["approval_id"], agent=doc.get("agent"))
    else:
        # Store tools run only from a ticket this backend wrote for a store device (origin "pos");
        # Sync Gateway lets nobody but the backend write one.
        if tool not in REQUESTABLE or (tool in STORE_ONLY_TOOLS and doc.get("origin") != "pos"):
            doc.update(status="failed", summary=f"'{tool}' can't be requested from the app", completed_at=now_iso())
            await store.upsert("orders", key, doc)
            return doc
        doc.update(status="processing", processing_at=now_iso())
        await store.upsert("orders", key, doc)
        # shopper_id comes from the document, which Sync Gateway only accepted
        # from that signed-in shopper (requireUser in the sync function).
        args = {k: v for k, v in (doc.get("args") or {}).items() if k != "shopper_id"}
        result = await tool_gateway.invoke(tool, {**args, "shopper_id": shopper_id}, agent=doc.get("agent"))

    status = result.get("status", "failed")
    if result.get("ok") and status in ("ok", "confirmed", "partially_shipped"):
        status = "confirmed" if status == "ok" else status
    if not result.get("ok"):
        status = "failed"
    doc.update({
        "status": status, "summary": result.get("summary"), "charge_usd": result.get("charge_usd"),
        "item": result.get("item"), "reason_code": result.get("reason_code"), "purchase_limits": result.get("purchase_limits"),
        "governed": result.get("governed", False), "trace_id": result.get("trace_id"),
        "approval_id": result.get("approval_id", doc.get("approval_id")),
        "completed_at": None if status == "awaiting_approval" else now_iso(),
        "channels": ([store_channel(doc.get("store_id") or "-")] + ([shopper_channel(shopper_id)] if shopper_id else []))
        if doc.get("origin") == "pos" else [shopper_channel(shopper_id)],
    })
    await store.upsert("orders", key, doc)
    metrics.order(doc, first)
    log.info("order %s %s -> %s", key, tool, status)
    return doc


async def run_once() -> int:
    rows = await store.pending_orders()
    for key, doc in rows:
        try:
            await process(key, doc)
        except Exception as exc:  # noqa: BLE001
            log.exception("order %s failed", key)
            doc.update(status="failed", summary=f"Something went wrong: {exc}", completed_at=now_iso())
            await store.upsert("orders", key, doc)
    return len(rows)


async def loop():
    while True:
        try:
            await run_once()
        except Exception as exc:  # noqa: BLE001
            log.warning("worker tick failed: %s", exc)
        await asyncio.sleep(config.REQUEST_POLL_SECONDS)
