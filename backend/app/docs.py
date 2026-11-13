"""Document ids, channel names and small shared helpers."""
from __future__ import annotations

import datetime as dt

from . import config

STATE = {"export_date": None}  # set by the loader


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> dt.date:
    """The demo's notion of 'today' - see config.DEMO_TODAY."""
    if config.DEMO_TODAY:
        return dt.date.fromisoformat(config.DEMO_TODAY)
    if STATE.get("export_date"):
        return dt.date.fromisoformat(STATE["export_date"])
    return dt.date.today()


# Channels. A shopper's Sync Gateway user gets exactly three: their own
# channel (profile, recommendations, orders, assistant chat, context cache),
# their home store's channel (hours, services, aisle map) and the shared
# catalog.
def shopper_channel(shopper_id: str) -> str:
    return f"shopper:{shopper_id}"


def store_channel(store_id: str) -> str:
    return f"store:{store_id}"


CATALOG_CHANNEL = "catalog"


def shopper_id_doc(s):
    return f"shopper::{s}"


def store_id_doc(s):
    return f"store::{s}"


def recs_id(s):
    return f"recs::{s}"


def chat_id(s):
    return f"chat::{s}"


def ctx_id(shopper_id, key):
    """key = the intent for the shopper's own context, guide_<intent> for
    Shopping Guide context (no shopper data in it)."""
    return f"ctx::{shopper_id}::{key}"


def inventory_id(fc):
    return f"inv::{fc}"


CATALOG_ID = "catalog::v1"


def days_since(date_str: str | None) -> int | None:
    if not date_str:
        return None
    return (today() - dt.date.fromisoformat(date_str[:10])).days


def days_until(date_str: str | None) -> int | None:
    if not date_str:
        return None
    return (dt.date.fromisoformat(date_str[:10]) - today()).days


def money(x: float) -> str:
    return f"${x:,.2f}"
