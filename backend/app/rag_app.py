"""The shopping assistant's registration as an AOM **RAG Application**.

AOM's RAG Applications page lists registered apps (`/v1/rag/apps`) and
their activity: queries, LLM cache hits and Context Cache retrievals, all
attributed by the `rag:<app_id>:` namespace prefix.

The app is scoped to exactly one Knowledge Base document, today's Shopping
Guide, because a RAG query answers from whatever its role can read, and the
agent's role can also read every shopper profile the daily loader publishes.
Without the scope, a generic question from one shopper could retrieve
another shopper's profile. Shopper questions don't go through the app's
/query endpoint. The assistant builds those itself, with retrieval pinned to
the shopper's own profile document.

The guide document gets a new id every day, so the loader calls `sync()`
after each publish to point the app at the new one. `scripts/register_with_aom.py`
creates the app the first time.
"""
from __future__ import annotations

import logging

from . import config
from .aom_client import AOMError, aom

log = logging.getLogger("pos.rag_app")

STATUS: dict = {"state": "not_synced"}

SYSTEM_PROMPT = (
    "You are the ShopSmart POS Assistant in the Couchbase ShopSmart mobile app. Answer the shopper's question "
    "directly and concisely (under 110 words) using only the numbered Shopping Guide passages. Never invent "
    "prices, stock or orders. Cite the passages you used as [1], [2]. If the passages do not answer the "
    "question, say the shopper should contact Customer Care."
)


def definition(guide_document_id: str | None) -> dict:
    return {
        "app_id": config.RAG_APP_ID,
        "name": "ShopSmart POS Assistant (mobile)",
        "description": ("Couchbase ShopSmart mobile shopping assistant. Policy questions are answered here from the "
                        "daily Shopping Guide. Personal answers use the shopper's own profile from the "
                        "Databricks export, cached in the shopper-context namespace."),
        "owner": "ShopSmart Store Systems",
        "enabled": bool(guide_document_id),
        "allowed_roles": ["sales_agent", "admin"],
        "set_id": "default",
        "document_ids": [guide_document_id] if guide_document_id else [],
        "top_k": 4,
        "min_score": 0.0,
        "retrieval_ttl_seconds": 6 * 3600,
        "semantic_cache": True,
        "system_prompt": SYSTEM_PROMPT,
        "no_answer_text": "I couldn't find that in the Shopping Guide. Customer Care can help.",
    }


async def sync(guide_document_id: str | None) -> dict:
    """Create the app, or update it in place (same app_id, same activity history)."""
    if not (config.RAG_APP_ENABLED and aom.admin_enabled):
        STATUS.update(state="disabled")
        return STATUS
    app = definition(guide_document_id)
    try:
        try:
            await aom.admin("POST", "/v1/rag/apps", {"app": app, "issue_key": False})
            action = "registered"
        except AOMError as exc:
            if exc.status != 409:
                raise
            await aom.admin("PUT", f"/v1/rag/apps/{config.RAG_APP_ID}", {"app": app})
            action = "updated"
        STATUS.update(state=action if app["enabled"] else "waiting_for_guide", app_id=config.RAG_APP_ID,
                      document_ids=app["document_ids"], error=None)
        log.info("RAG application %s %s (guide %s)", config.RAG_APP_ID, action, guide_document_id)
    except AOMError as exc:
        # 404 on the route = an AOM build without RAG Applications.
        STATUS.update(state="unsupported" if exc.status in (404, 405) else "error", error=str(exc)[:300])
        log.warning("RAG application sync failed: %s", exc)
    return STATUS


def usable() -> bool:
    # "disabled" only means this backend has no admin login to manage the app;
    # the register script may still have created it, so a query is worth trying.
    return (aom.enabled and config.RAG_APP_ENABLED
            and STATUS.get("state") not in ("unsupported", "waiting_for_guide", "query_rejected"))


def query_failed(exc: AOMError):
    """404 (no such app / old AOM) or 403 (disabled, role not allowed): stop
    trying until the next sync, and use the assistant's own path instead."""
    if exc.status in (403, 404, 405):
        STATUS.update(state="query_rejected", error=str(exc)[:300])
