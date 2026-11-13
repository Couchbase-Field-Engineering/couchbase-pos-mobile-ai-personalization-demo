"""`shopsmart-pos` - the MCP server registered with AOM.

Streamable HTTP at /pos/mcp/. AOM ingests these tool definitions into
its vetted catalog (embeddings for discovery, RBAC per role, drift detection)
and is the only caller the demo expects: the app and assistant reach these
tools through `POST /v1/tools/invoke`, never directly.

Business errors (purchase limit, unknown SKU, bad delivery option) come back
as `{"ok": false, ...}` results rather than MCP errors, so AOM records a
completed call and the shopper gets a useful message instead of a gateway
failure.
"""
from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import actions

log = logging.getLogger("pos.mcp")

mcp = FastMCP(
    "shopsmart-pos",
    instructions=("Couchbase ShopSmart, in the app and in the store. For a signed-in shopper: personalized recommendations and "
                  "smart cart, member pricing, fulfillment-center availability, order placement, price matches, Subscribe & "
                  "Save, returns, goodwill credits and preferences. For the store: recognizing a guest at the drive-thru "
                  "lane, on the phone line or at self-checkout, ringing up their order, and settling self-checkout exceptions."),
    stateless_http=True,
    # Called by AOM over the Docker host gateway, so the Host header is not
    # localhost; same reasoning as AOM's own bundled sample servers.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


async def _run(fn, **kw) -> dict:
    try:
        res = await fn(**kw)
    except actions.ToolError as exc:
        return {"ok": False, "status": "failed", "summary": str(exc)}
    return res if isinstance(res, dict) and "ok" in res else {"ok": True, "status": "ok", "data": res}


@mcp.tool()
async def get_shopper_context(shopper_id: str) -> dict:
    """Read a shopper's profile: household, membership tier, wallet (store credit, points), delivery defaults, home store, preferences, last order, subscriptions and model segment."""
    return await _run(actions.get_shopper_context, shopper_id=shopper_id)


@mcp.tool()
async def get_recommendations(shopper_id: str) -> dict:
    """The next-basket model's smart cart for a shopper (what is about to run out), priced for them with on-hand units at their fulfillment center, plus 'for you' picks, price-match candidates, their personal offers from loyalty data and the deals active right now (overstock markdowns, automatic competitor matches, the promotion for this hour, bundles)."""
    return await _run(actions.get_recommendations, shopper_id=shopper_id)


@mcp.tool()
async def search_catalog(shopper_id: str, query: str = "", category: str = "") -> dict:
    """Search the product catalog for a shopper: their unit price with the deals behind it, list price, fulfillment-center on-hand units, aisle at their home store, purchase limit and whether they buy it regularly. category is one of pantry, household, personal, baby, pets, kitchen, electronics, toys, outdoor."""
    return await _run(actions.search_catalog, shopper_id=shopper_id, query=query, category=category)


@mcp.tool()
async def quote_cart(shopper_id: str, lines: list, delivery_option: str = "") -> dict:
    """Price a cart of lines [{sku, qty}] for a shopper: member pricing, multi-buy savings, dynamic promotions, bundles and personal offers (each line lists its adjustments), delivery fee, tax, store credit applied, points earned, purchase-limit check and any short stock."""
    return await _run(actions.quote_cart, shopper_id=shopper_id, lines=lines, delivery_option=delivery_option)


@mcp.tool()
async def place_order(shopper_id: str, lines: list, delivery_option: str = "", notes: str = "", pricing_version: str = "",
                      priced_hour: int | None = None, sources: dict | None = None) -> dict:
    """Place an order of lines [{sku, qty}] for a shopper at their price (member tier, promotions, bundles, offers). pricing_version and priced_hour lock the price to the rules the shopper's phone priced the cart with; sources maps sku to how it got into the cart (smart_cart, pick, cross_sell, upsell, bundle, deal, browse). Allocates fulfillment-center inventory (backorders the rest), applies store credit and awards points. Fails with reason_code purchase_limit when a limited item exceeds its per-household limit."""
    return await _run(actions.place_order, shopper_id=shopper_id, lines=lines, delivery_option=delivery_option, notes=notes,
                      pricing_version=pricing_version, priced_hour=priced_hour, sources=sources)


@mcp.tool()
async def request_price_match(shopper_id: str, lines: list, sku: str, competitor: str, competitor_price_usd: float,
                              delivery_option: str = "", notes: str = "", pricing_version: str = "",
                              priced_hour: int | None = None, sources: dict | None = None) -> dict:
    """Place an order of lines [{sku, qty}] with one item (sku) matched to a competitor's lower price. REQUIRES HUMAN APPROVAL (Customer Care). Competitor must be one ShopSmart matches; the matched price has a floor and a per-order cap."""
    return await _run(actions.request_price_match, shopper_id=shopper_id, lines=lines, sku=sku, competitor=competitor,
                      competitor_price_usd=competitor_price_usd, delivery_option=delivery_option, notes=notes,
                      pricing_version=pricing_version, priced_hour=priced_hour, sources=sources)


@mcp.tool()
async def set_subscription(shopper_id: str, sku: str, qty: int, cadence_weeks: int = 4) -> dict:
    """Put an everyday essential on Subscribe & Save for a shopper: qty every 1, 2, 4, 6, 8 or 12 weeks at member price. Subscribed items leave the smart cart."""
    return await _run(actions.set_subscription, shopper_id=shopper_id, sku=sku, qty=qty, cadence_weeks=cadence_weeks)


@mcp.tool()
async def create_return(shopper_id: str, order_id: str, sku: str, qty: int, reason: str) -> dict:
    """Start a return for an item on one of the shopper's orders from the last 30 days. Refunds the member price paid to store credit."""
    return await _run(actions.create_return, shopper_id=shopper_id, order_id=order_id, sku=sku, qty=qty, reason=reason)


@mcp.tool()
async def update_preferences(shopper_id: str, favorite_categories: list | None = None, avoid_categories: list | None = None,
                             default_delivery_option: str = "", allow_substitutions: bool | None = None) -> dict:
    """Save what a shopper wants more of or hidden (category codes), their default delivery option and whether substitutions are allowed. Re-ranks their recommendations."""
    return await _run(actions.update_preferences, shopper_id=shopper_id, favorite_categories=favorite_categories,
                      avoid_categories=avoid_categories, default_delivery_option=default_delivery_option,
                      allow_substitutions=allow_substitutions)


@mcp.tool()
async def issue_goodwill_credit(shopper_id: str, amount_usd: float, reason: str) -> dict:
    """Add a goodwill store credit (up to $100) to a shopper's wallet for a service problem. REQUIRES HUMAN APPROVAL (Customer Care)."""
    return await _run(actions.issue_goodwill_credit, shopper_id=shopper_id, amount_usd=amount_usd, reason=reason)


# --------------------------------------------------------------------------
# store tools: the voice ordering agent and the self-checkout vision agent
# --------------------------------------------------------------------------
@mcp.tool()
async def identify_guest(shopper_id: str = "", member_number: str = "", store_id: str = "") -> dict:
    """Recognize the guest at a drive-thru lane, on the phone line or at a self-checkout kiosk, by shopper id (app check-in, caller ID) or Rewards member number. Returns their tier, wallet, their usual café order priced for them at this store, what the nightly model says they are about to run out of, and their personal offers. identified is false for a walk-up guest."""
    return await _run(actions.identify_guest, shopper_id=shopper_id, member_number=member_number, store_id=store_id)


@mcp.tool()
async def place_pos_order(store_id: str, channel: str, lines: list, shopper_id: str = "", payment: str = "", lane: str = "",
                          session_id: str = "", notes: str = "", sources: dict | None = None, pricing_version: str = "",
                          priced_hour: int | None = None) -> dict:
    """Ring up a store order. channel is drive_thru, phone or self_checkout. lines are [{sku, qty}] for retail items and [{sku, qty, size, mods}] for café menu items (CF-...). Priced with the store's current rules: member tier when shopper_id is given, the promotion for the hour, combos, bundles, fresh-batch markdowns and personal offers; pricing_version and priced_hour lock the price to what the store device quoted. sources maps a line to what led to it (usual, voice, upsell, bundle, deal, smart_cart, vision, scan). Updates store and kitchen counts and, for a member, their wallet, points, history and recommendations. Fails with reason_code purchase_limit or sold_out."""
    return await _run(actions.place_pos_order, shopper_id=shopper_id, store_id=store_id, channel=channel, lines=lines, payment=payment,
                      lane=lane, session_id=session_id, notes=notes, sources=sources, pricing_version=pricing_version,
                      priced_hour=priced_hour)


@mcp.tool()
async def resolve_checkout_exception(store_id: str, session_id: str, exception_id: str, decision: str, shopper_id: str = "",
                                     attendant: str = "", note: str = "") -> dict:
    """Settle a self-checkout exception the vision agent raised (a barcode that doesn't match what the camera and scale see, or an item bagged without a scan). decision is charge_as_seen, accept_scan or remove. REQUIRES HUMAN APPROVAL (the store's front-end attendant)."""
    return await _run(actions.resolve_checkout_exception, shopper_id=shopper_id, store_id=store_id, session_id=session_id,
                      exception_id=exception_id, decision=decision, attendant=attendant, note=note)
