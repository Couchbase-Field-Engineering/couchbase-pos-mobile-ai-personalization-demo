"""Sync Gateway admin calls: one SG user per shopper, and session minting.

The shopper signs in to this backend (member number + last name); the backend
makes sure an SG user named after the shopper_id exists with exactly four
channels - `shopper:<shopper_id>`, `store:<home store>`, `fc:<fulfillment
center>` (the pricing rules) and `catalog` - and
mints a session. The phone hands that session to Couchbase Lite's
SessionAuthenticator, the same way mobile_field_service does, so a shopper's
replicator can only ever pull their own profile, recommendations and orders,
their home store, their fulfillment center's pricing rules and the catalog.
"""
from __future__ import annotations

import logging
import secrets

import httpx

from . import config
from .docs import CATALOG_CHANNEL, shopper_channel, store_channel

log = logging.getLogger("pos.sgw")


def enabled() -> bool:
    return bool(config.SG_ADMIN_URL)


async def mint_session(shopper_id: str, store_id: str | None, fc: str | None = None) -> dict | None:
    if not enabled():
        return None
    auth = (config.SG_ADMIN_USERNAME, config.SG_ADMIN_PASSWORD)
    base = f"{config.SG_ADMIN_URL}/{config.SG_DB}"
    async with httpx.AsyncClient(timeout=10, auth=auth) as c:
        channels = ([shopper_channel(shopper_id), CATALOG_CHANNEL] + ([store_channel(store_id)] if store_id else [])
                    + ([f"fc:{fc}"] if fc else []))
        user = {"name": shopper_id, "password": secrets.token_urlsafe(24)}
        # Collection-scoped channel grants (SG 3.x named collections).
        user["collection_access"] = {
            config.COUCHBASE_SCOPE: {col: {"admin_channels": channels}
                                     for col in ("shoppers", "stores", "recommendations", "orders", "assistant",
                                                 "context_cache", "catalog")}}
        r = await c.put(f"{base}/_user/{shopper_id}", json=user)
        if r.status_code >= 400:
            log.warning("SG user upsert failed: %s %s", r.status_code, r.text[:200])
            return None
        r = await c.post(f"{base}/_session", json={"name": shopper_id, "ttl": config.SG_SESSION_TTL_SECONDS})
        if r.status_code >= 400:
            log.warning("SG session failed: %s %s", r.status_code, r.text[:200])
            return None
        s = r.json()
        return {"session_id": s.get("session_id"), "cookie_name": s.get("cookie_name", "SyncGatewaySession"),
                "expires": s.get("expires"), "db": config.SG_DB, "url": config.SG_PUBLIC_URL or None}
