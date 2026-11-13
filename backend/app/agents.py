"""The three AI agents of the demo, as they are registered with AOM.

Each has its own identity, its own API key and only the tools its job needs,
so AOM's Agents, Traces and Audit pages show who did what:

* the **assistant** in the guest's phone app,
* the **voice ordering agent** on the drive-thru lane and the phone line,
* the **self-checkout vision agent** on the kiosks.

Shared by aom_bootstrap.py (self-registration) and scripts/register_with_aom.py.
"""
from __future__ import annotations

OWNER = "ShopSmart Store Systems"

AGENTS = {
    "assistant": {
        "name": "ShopSmart POS Assistant", "header_id": "shopsmart-pos-assistant", "role": "sales_agent",
        "env": "AOM_AGENT_API_KEY", "key_doc": "aom_agent",
        "description": "The assistant in the ShopSmart guest app: personalized recommendations, RAG over the daily Databricks "
                       "shopper profiles, and shopping actions (orders, price matches, Subscribe & Save, returns, preferences).",
        "tools": ["get_shopper_context", "get_recommendations", "search_catalog", "quote_cart", "place_order", "request_price_match",
                  "set_subscription", "create_return", "update_preferences", "issue_goodwill_credit"],
    },
    "voice": {
        "name": "ShopSmart POS Voice Ordering Agent", "header_id": "shopsmart-pos-voice", "role": "sales_agent",
        "env": "AOM_VOICE_AGENT_API_KEY", "key_doc": "aom_agent_voice",
        "description": "Takes spoken orders at the drive-thru lane and on the store phone line: recognizes the guest, offers "
                       "their usual, makes one merchandising offer at a time, and rings the order up.",
        "tools": ["identify_guest", "search_catalog", "quote_cart", "place_pos_order"],
    },
    "vision": {
        "name": "ShopSmart POS Self-Checkout Vision Agent", "header_id": "shopsmart-pos-vision", "role": "sales_agent",
        "env": "AOM_VISION_AGENT_API_KEY", "key_doc": "aom_agent_vision",
        "description": "Recognizes items at self-checkout from the kiosk camera and the bagging scale, catches mismatched "
                       "and missed scans, and rings the order up. Exceptions it can't settle go to a person.",
        "tools": ["identify_guest", "place_pos_order", "resolve_checkout_exception"],
    },
}
