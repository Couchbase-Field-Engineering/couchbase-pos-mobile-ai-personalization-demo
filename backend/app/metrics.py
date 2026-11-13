"""In-process counters for the story page's live chapter (/api/status ->
metrics). They reset when the backend restarts; AOM keeps the durable view
(traces, cache pages, audit log).

The Databricks counters are the point of the demo for a Databricks shop:
every profile read, recommendation, assistant context and order the app
serves from Couchbase (or from Couchbase Lite on the phone) is a query the
SQL warehouse or model-serving endpoint never had to run. The export lands
once a night; everything after that is served from the data plane.
"""
from __future__ import annotations

import datetime as dt
import os

# Illustrative cost of one interactive query against a Databricks SQL
# warehouse / Model Serving call (compute + egress), used only to turn the
# avoided-query count into a dollar figure on the story page. Tune it to the
# customer's real per-query cost.
DBX_COST_PER_QUERY_USD = float(os.getenv("DBX_COST_PER_QUERY_USD", "0.035"))

STATS: dict = {
    "assistant_turns": 0, "context_hits": 0, "context_misses": 0, "rag_app_turns": 0,
    "llm_calls": 0, "llm_hits": 0,
    "orders": 0, "orders_confirmed": 0, "orders_failed": 0, "orders_awaiting_approval": 0, "order_value_usd": 0.0,
    # Created on the phone well before the backend saw it: queued in
    # Couchbase Lite while the shopper had no connection.
    "orders_queued_offline": 0,
    # Databricks offload: reads answered from Couchbase / Couchbase Lite that
    # would otherwise be warehouse queries (shopper 360, recommendations,
    # pricing, order history) or Model Serving calls (next-basket model).
    "warehouse_queries_avoided": 0,
    "model_serving_calls_avoided": 0,
    "sync_pulls": 0,
    "export_loads": 0,
    "since": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
OFFLINE_GAP_SECONDS = 20

# Merchandising: what each way of getting an item into the cart sold, and what
# each kind of promotion gave away. `source` on an order line is set by the
# app when the shopper adds the item (smart cart, a pick, a cross-sell at
# checkout, an upsell, a bundle prompt, a deal, plain browsing).
MERCH: dict = {"revenue_by_source": {}, "units_by_source": {}, "discount_by_rule": {}, "bundles_sold": 0,
               "offers_redeemed": 0, "bonus_points_awarded": 0, "orders_with_promotion": 0, "orders_price_locked": 0}


# The store side: what the voice ordering agent and the self-checkout vision
# agent did. Counted since the backend started, like everything else here.
POS: dict = {
    "orders_by_channel": {}, "revenue_by_channel": {}, "tickets": 0, "tickets_queued_on_edge": 0,
    # voice ordering (drive-thru lane + phone line)
    "voice_sessions": 0, "voice_turns": 0, "voice_turns_on_edge": 0, "voice_llm_turns": 0, "voice_turn_ms_total": 0.0,
    "voice_guests_recognized": 0, "voice_offers_made": 0, "voice_offers_accepted": 0, "voice_offer_revenue_usd": 0.0,
    "voice_orders": 0,
    # computer-vision-assisted self-checkout
    "kiosk_sessions": 0, "kiosk_items": 0, "kiosk_auto_recognized": 0, "kiosk_resolved_by_scale": 0, "kiosk_asked_guest": 0,
    "kiosk_barcode_fallback": 0, "kiosk_exceptions": 0, "kiosk_exceptions_resolved": 0, "kiosk_nudges_accepted": 0,
    "kiosk_shrink_prevented_usd": 0.0, "kiosk_orders": 0, "kiosk_checkout_seconds_total": 0.0,
}
# Seconds a guest spends finding and scanning one barcode at a classic kiosk: the time a recognized item saves.
SECONDS_PER_BARCODE_SCAN = 4.0


def pos_order(order_item: dict) -> None:
    ch = order_item.get("channel") or "store"
    POS["orders_by_channel"][ch] = POS["orders_by_channel"].get(ch, 0) + 1
    POS["revenue_by_channel"][ch] = round(POS["revenue_by_channel"].get(ch, 0) + float(order_item.get("total_usd") or 0), 2)
    POS["voice_orders" if ch in ("drive_thru", "phone") else "kiosk_orders"] += 1
    # Menu, pricing rules, the guest's profile and offers were read at the store, not from a warehouse.
    STATS["warehouse_queries_avoided"] += 3


def pos_ticket(doc: dict) -> None:
    POS["tickets"] += 1
    if doc.get("queued_on_edge"):
        POS["tickets_queued_on_edge"] += 1
        STATS["orders_queued_offline"] += 1


def voice_turn(ms: float, on_edge: bool, llm: bool) -> None:
    POS["voice_turns"] += 1
    POS["voice_turn_ms_total"] = round(POS["voice_turn_ms_total"] + ms, 2)
    POS["voice_turns_on_edge"] += 1 if on_edge else 0
    POS["voice_llm_turns"] += 1 if llm else 0


def bump(key: str, by: float = 1) -> None:
    POS[key] = round(POS[key] + by, 2) if isinstance(by, float) else POS[key] + by


def merchandising(order_item: dict) -> None:
    for l in order_item.get("lines", []):
        src = l.get("source") or "browse"
        MERCH["revenue_by_source"][src] = round(MERCH["revenue_by_source"].get(src, 0) + l["line_total"], 2)
        MERCH["units_by_source"][src] = MERCH["units_by_source"].get(src, 0) + l["qty"]
    for p in order_item.get("promotions", []):
        MERCH["discount_by_rule"][p["label"]] = round(MERCH["discount_by_rule"].get(p["label"], 0) + p["saved_usd"], 2)
    MERCH["bundles_sold"] += len(order_item.get("bundles_applied", []))
    MERCH["offers_redeemed"] += len(order_item.get("offers_applied", []))
    MERCH["bonus_points_awarded"] += int(order_item.get("bonus_points") or 0)
    MERCH["orders_with_promotion"] += 1 if order_item.get("promotions") else 0
    MERCH["orders_price_locked"] += 1 if order_item.get("price_locked") else 0
    # Pricing rules, offers and affinities for the order were read from Couchbase, not scored live.
    STATS["model_serving_calls_avoided"] += 1


def assistant(result: dict) -> None:
    STATS["assistant_turns"] += 1
    ctx = (result.get("context") or {}).get("status")
    if ctx == "hit":
        STATS["context_hits"] += 1
        # A cached context is an shopper-360 + recommendation read that never went
        # back to the warehouse.
        STATS["warehouse_queries_avoided"] += 2
        STATS["model_serving_calls_avoided"] += 1
    elif ctx:
        STATS["context_misses"] += 1
        # Even a miss is assembled from the nightly export in Couchbase, not
        # from a live warehouse query.
        STATS["warehouse_queries_avoided"] += 1
    if (result.get("context") or {}).get("source") == "aom-rag":
        STATS["rag_app_turns"] += 1
    llm = (result.get("llm") or {}).get("status") or ""
    if llm and llm not in ("local", "error", "skipped"):
        STATS["llm_calls"] += 1
        if llm.startswith("hit"):
            STATS["llm_hits"] += 1


def sync_pull(doc_count: int) -> None:
    """A phone (or the browser preview) pulled its documents: profile, recommendations,
    orders, catalog, store. In a warehouse-backed app each of those screens
    is a query; here they are one replication."""
    STATS["sync_pulls"] += 1
    STATS["warehouse_queries_avoided"] += max(1, doc_count)
    STATS["model_serving_calls_avoided"] += max(0, doc_count // 3)


def export_loaded(rows: int) -> None:
    STATS["export_loads"] += 1


def order(doc: dict, first: bool) -> None:
    """Count an order's outcome. `first` = its first pass through the worker
    (an approval that resolves later is a second pass of the same order)."""
    status = doc.get("status")
    if status in ("processing", "pending"):
        return
    if first:
        STATS["orders"] += 1
        # Pricing, wallet and history lookup for the order came from Couchbase.
        STATS["warehouse_queries_avoided"] += 3
    key = {"confirmed": "orders_confirmed", "partially_shipped": "orders_confirmed",
           "failed": "orders_failed", "awaiting_approval": "orders_awaiting_approval"}.get(status)
    if key:
        STATS[key] += 1
    if status in ("confirmed", "partially_shipped"):
        STATS["order_value_usd"] = round(STATS["order_value_usd"] + float(doc.get("charge_usd") or 0), 2)
    if not first:
        return
    try:
        created = dt.datetime.fromisoformat(str(doc.get("created_at")).replace("Z", "+00:00"))
        if (dt.datetime.now(dt.timezone.utc) - created).total_seconds() > OFFLINE_GAP_SECONDS:
            STATS["orders_queued_offline"] += 1
    except (TypeError, ValueError):
        pass


def snapshot() -> dict:
    s = dict(STATS)
    turns = s["context_hits"] + s["context_misses"]
    s["context_hit_rate"] = round(100 * s["context_hits"] / turns, 1) if turns else None
    s["llm_hit_rate"] = round(100 * s["llm_hits"] / s["llm_calls"], 1) if s["llm_calls"] else None
    s["dbx_cost_per_query_usd"] = DBX_COST_PER_QUERY_USD
    led = MERCH["revenue_by_source"]
    s["merchandising"] = {**MERCH, "recommendation_led_revenue_usd": round(
        sum(v for k, v in led.items() if k in ("pick", "cross_sell", "upsell", "bundle", "deal", "offer", "smart_cart")), 2)}
    pos = {k: (dict(v) if isinstance(v, dict) else v) for k, v in POS.items()}
    pos["voice_avg_turn_ms"] = round(POS["voice_turn_ms_total"] / POS["voice_turns"], 1) if POS["voice_turns"] else None
    pos["voice_offer_accept_rate"] = round(100 * POS["voice_offers_accepted"] / POS["voice_offers_made"], 1) if POS["voice_offers_made"] else None
    pos["kiosk_auto_rate"] = round(100 * (POS["kiosk_auto_recognized"] + POS["kiosk_resolved_by_scale"]) / POS["kiosk_items"], 1) if POS["kiosk_items"] else None
    pos["kiosk_scans_avoided"] = POS["kiosk_auto_recognized"] + POS["kiosk_resolved_by_scale"]
    pos["kiosk_seconds_saved"] = round(pos["kiosk_scans_avoided"] * SECONDS_PER_BARCODE_SCAN, 1)
    s["pos"] = pos
    s["estimated_dbx_spend_avoided_usd"] = round(
        (s["warehouse_queries_avoided"] + s["model_serving_calls_avoided"]) * DBX_COST_PER_QUERY_USD, 2)
    return s
