"""Knowledge Base documents published to AOM by the daily loader.

Two kinds:

* **Shopper profile** - one per recently active shopper, rendered from that
  morning's Databricks row as plain prose. The assistant retrieves it with a
  `document_id`-scoped search, so a question from one shopper can only ever
  be grounded in *their own* profile, never another shopper's that happens to
  be semantically close.
* **Shopping Guide** - membership, pricing, delivery, returns, price match
  and purchase-limit policy. Contains no customer data, so answers grounded
  only in it are safe to share across shoppers through AOM's LLM cache.

FAQ entries are also synced to the phone inside the catalog document, so the
app can answer the common questions offline from Couchbase Lite.
"""
from __future__ import annotations

from . import catalog as C

FAQ = [
    {"q": "How does member pricing work?",
     "a": "ShopSmart Rewards has three tiers: Member (free, list price, 1 point per dollar), Plus (5% off every item, 2 points "
          "per dollar) and Premier (10% off, 3 points per dollar). Multi-buy savings stack on top: 3 or more of the same item "
          "takes another 5% off, 6 or more another 10%. The app prices every item from your tier on the phone, so the total "
          "is right with or without signal.",
     "keywords": ["member", "pricing", "price", "discount", "tier", "plus", "premier", "multi-buy", "membership", "points"]},
    {"q": "What are the delivery options?",
     "a": "Same-day delivery, 2-day shipping, scheduled delivery and store pickup. Delivery is free on every Plus and Premier "
          "order and on Member orders over $35 (otherwise $5.99). Store pickup is always free and is usually ready in about "
          "two hours at your home store.",
     "keywords": ["delivery", "deliver", "ship", "shipping", "same-day", "pickup", "pick up", "arrive", "when will", "eta", "fee"]},
    {"q": "What happens when an item is out of stock?",
     "a": "The order still goes through. Available units ship from your fulfillment center and the rest is backordered with an "
          "ETA on the order confirmation; you are only charged for what ships. The app shows how many are on hand next to "
          "each item so you can pick something else before you check out.",
     "keywords": ["out of stock", "backorder", "stock", "available", "availability", "on hand", "substitute", "sold out"]},
    {"q": "How does a price match work?",
     "a": "If Brightway Market, Northfield Supply or Oakline Stores has the identical item for less than your member price, "
          "check out with a price match request. Customer Care reviews it in the Agent Operations Manager; once approved the "
          "item is re-priced and the order is placed automatically. A matched price can't go below 70% of list and a match "
          "saves at most $150 per order.",
     "keywords": ["price match", "match", "cheaper", "competitor", "lower price", "beat", "found it for less"]},
    {"q": "What is the returns policy?",
     "a": "Most items can be returned within 30 days of delivery. Start the return in the app and the refund lands in your "
          "store credit right away; drop the item at the returns desk at any store or use the prepaid label within 14 days. "
          "Damaged-on-arrival items can be credited without a return - ask the assistant for a goodwill credit.",
     "keywords": ["return", "returns", "refund", "damaged", "wrong item", "send back", "exchange"]},
    {"q": "How does Subscribe & Save work?",
     "a": "Put any everyday essential on a schedule: a fixed quantity every 1, 2, 4, 6, 8 or 12 weeks at your member price, "
          "delivered with your default delivery option. Pause or change it any time. Subscribed items are left out of your "
          "smart cart so you never double up.",
     "keywords": ["subscribe", "subscription", "recurring", "auto", "schedule", "every week", "every month", "cadence", "save"]},
    {"q": "How are my recommendations chosen?",
     "a": "Each night the next-basket model in Databricks looks at what you buy, how quickly you go through it and what "
          "shoppers like you add next, and writes a smart cart and a set of picks with a confidence for each. The daily "
          "export lands in Couchbase and syncs to your phone, where it is re-priced at your member tier and checked against "
          "stock. Every order and every preference you change re-ranks it on the next sync.",
     "keywords": ["recommend", "recommendation", "smart cart", "for you", "model", "why", "personalized", "suggest", "databricks"]},
    {"q": "Why can't I buy more than one of an item?",
     "a": "High-demand items such as the Nova X handheld console are limited to one per household every 30 days so more "
          "shoppers get a chance to buy one. An order that goes over the limit is not placed; remove the item and the rest "
          "of your cart checks out normally. Premier members get early access to limited releases, not a higher limit.",
     "keywords": ["limit", "limited", "one per", "more than one", "second", "restricted", "console", "household"]},
    {"q": "Why did a price change today?",
     "a": "Prices follow a few published rules on top of your member discount. An item that is overstocked at your "
          "fulfillment center is marked down 10 to 15%. Some promotions run only at certain hours: breakfast items are 10% "
          "off from 6 to 10 am, household essentials 5% off from 1 to 4 pm, and store pickup takes 5% off everything from 5 "
          "to 8 pm. A competitor price within 8% of ours is matched automatically; a bigger gap is a price-match request. "
          "Items that are nearly sold out or in high demand are never promoted. Discounts add up to at most 40% off list, "
          "and the price your phone showed when you built the cart is the price you pay, even if it syncs later.",
     "keywords": ["price change", "change", "changed", "why did", "why is", "dynamic", "went up", "went down", "different price", "price drop", "marked down",
                  "overstock", "happy hour", "peak"]},
    {"q": "How do bundles and personal offers work?",
     "a": "A bundle takes a percentage off every item in it when they are all in your cart, for example brewer and pods, or "
          "diapers and wipes; the cart tells you which item is missing and what adding it saves. A bundle pauses while one of "
          "its items is nearly sold out. Personal offers are chosen for you each night from your purchase history and "
          "membership: a discount on a category or item you buy, or bonus points over a spend level. They apply "
          "automatically, show in the cart total, and stack with member pricing up to 40% off list.",
     "keywords": ["bundle", "bundles", "offer", "offers", "promotion", "promo", "coupon", "bonus points", "stack", "combine"]},
    {"q": "What can I do in the app with no signal?",
     "a": "Everything you need to shop: browse the catalog, see your smart cart and picks, find the aisle at your home "
          "store, build and price a cart, and check out. The order is saved to Couchbase Lite on your phone as 'On device' and "
          "syncs through Sync Gateway the moment you have a connection - at the top of the subway stairs, in the parking lot "
          "or back on Wi-Fi. Confirmations, backorders and approvals come back to the same order card.",
     "keywords": ["signal", "offline", "no service", "connection", "wifi", "sync", "subway", "airplane", "garage"]},
    {"q": "How do store credit and points work?",
     "a": "Refunds and goodwill credits go to your store credit, which comes off your next order automatically. Points are "
          "earned on every order's subtotal at your tier's rate and show in your wallet after the order confirms. Goodwill "
          "credits up to $100 can be requested for a service problem and always need Customer Care approval.",
     "keywords": ["store credit", "credit", "points", "wallet", "rewards", "goodwill", "balance", "gift"]},
]


def guide_markdown(export_date: str) -> str:
    lines = [f"# Couchbase ShopSmart Shopping Guide (published {export_date})", "",
             "Membership, pricing, delivery, returns, price match and purchase-limit knowledge for the ShopSmart Shopping "
             "Assistant. Contains no customer data.", ""]
    lines += ["## Frequently asked questions", ""]
    for f in FAQ:
        lines += [f"**{f['q']}** {f['a']}", ""]
    lines += ["## ShopSmart Rewards tiers", ""]
    for t in C.MEMBER_TIERS:
        lines.append(f"- {t['name']} ({t['code']}): {t['discount_pct']}% off list, {t['points_per_usd']} point"
                     f"{'s' if t['points_per_usd'] > 1 else ''} per dollar, free delivery "
                     + ("on every order" if t["free_delivery_min_usd"] == 0 else f"over ${t['free_delivery_min_usd']}") + f". {t['blurb']}")
    lines += ["", "Multi-buy savings on a single item: " + ", ".join(f"{q}+ units an extra {e}%" for q, e in C.MULTI_BUY)
              + f". Delivery fee when not free: ${C.DELIVERY_FEE_USD}. Options: {', '.join(C.DELIVERY_OPTIONS)}. "
              f"{C.PICKUP_OPTION} is always free.", ""]
    lines += ["## Price match, credits and limits", "",
              f"ShopSmart matches {', '.join(C.COMPETITORS)} on identical items. A match needs Customer Care approval, can't go "
              f"below {C.PRICE_MATCH_FLOOR_PCT}% of list and saves at most ${C.PRICE_MATCH_MAX_SAVING_USD:,.0f} per order. Refunds "
              f"and goodwill credits (up to ${C.GOODWILL_MAX_USD:,.0f}, approval required) go to store credit and come off the "
              f"next order. Returns are accepted within {C.RETURN_WINDOW_DAYS} days. Limited items are restricted per household "
              f"over a rolling {C.PURCHASE_LIMIT_WINDOW_DAYS} days.", ""]
    lines += ["## Dynamic pricing, bundles and offers", "",
              "Overstock markdowns: " + ", ".join(f"{pct}% off at {ratio}x the normal stock level" for ratio, pct in C.OVERSTOCK_TIERS)
              + f". Promotions pause at {int(C.LOW_STOCK_RATIO * 100)}% of the normal level or for high-demand items. "
              f"Competitor prices at or above {C.AUTO_MATCH_MIN_PCT_OF_LIST}% of list are matched automatically. Discounts are "
              f"capped at {C.MAX_DISCOUNT_PCT}% off list. An order is priced with the rules its phone used (price lock, 48 hours).", ""]
    for w in C.TIME_WINDOWS:
        lines.append(f"- {w['label']} ({w['start_hour']}:00-{w['end_hour']}:00): {w['reason']}.")
    for b in C.BUNDLES:
        lines.append(f"- {b['name']} bundle: " + " + ".join(C.PRODUCT_BY_SKU[x]['name'].split(',')[0] for x in b["skus"])
                     + f", {b['pct_off']}% off each. {b['reason']}.")
    lines += ["", "## Products", ""]
    for code, cat in C.CATEGORIES.items():
        items = [p for p in C.PRODUCTS if p["category"] == code]
        if not items:
            continue
        lines.append(f"### {cat['name']}")
        for p in items:
            lines.append(f"- {p['name']} ({p['sku']}): ${p['list_price']} list per {p['uom']}, {p['pack']}"
                         + ("; everyday essential, eligible for Subscribe & Save." if p["consumable"] else "; one-time purchase.")
                         + (f" Limit {p['limit_per_household']} per household." if p.get("limit_per_household") else ""))
        lines.append("")
    lines += ["## Fulfillment centers", ""]
    for code, fc in C.FULFILLMENT_CENTERS.items():
        lines.append(f"- {fc['name']} ({code}), {fc['city']}: ships to {', '.join(fc['states'])}.")
    lines += ["", "## Stores", "",
              f"{len(C.STORES)} stores across " + ", ".join(r["name"] for r in C.REGIONS.values())
              + ". Every store offers order pickup, curbside pickup and a returns desk."]
    return "\n".join(lines)


def shopper_profile_markdown(s: dict) -> str:
    m = s["ml_scores"]
    w = s["wallet"]
    hist = s.get("order_history", [])[:6]
    hist_txt = "; ".join(f"{o['order_id']} on {o['date']} ${o['total_usd']:,.2f} ({o.get('line_count', len(o.get('lines', [])))} items, "
                         f"{o.get('delivery_option', 'delivery')})" for o in hist) or "no orders yet"
    rep = sorted(s.get("replenishment", {}).items(), key=lambda kv: -float(kv[1].get("avg_monthly_qty") or 0))[:8]
    rep_txt = "; ".join(f"{C.PRODUCT_BY_SKU[sku]['name'].split(',')[0]} ~{v.get('avg_monthly_qty', 0):g} "
                        f"{C.PRODUCT_BY_SKU[sku]['uom']}/month, last {v.get('last_ordered')}" for sku, v in rep if sku in C.PRODUCT_BY_SKU)
    cart = "; ".join(f"{int(l['qty'])} x {C.PRODUCT_BY_SKU[l['sku']]['name'].split(',')[0]} ({l.get('confidence', 0):.0%})"
                     for l in m.get("replenish_lines", []) if l["sku"] in C.PRODUCT_BY_SKU)
    picks = "; ".join(f"{C.PRODUCT_BY_SKU[r['sku']]['name'].split(',')[0]} ({r.get('reason')})"
                      for r in m.get("recommended", []) if r["sku"] in C.PRODUCT_BY_SKU) or "none"
    watch = "; ".join(f"{C.PRODUCT_BY_SKU[x['sku']]['name'].split(',')[0]} at {x['competitor']} for ${x['competitor_price_usd']:,.2f}"
                      for x in m.get("price_watch", []) if x["sku"] in C.PRODUCT_BY_SKU) or "none"
    subs = "; ".join(f"{x['qty']} x {x['name'].split(',')[0]} every {x['cadence_weeks']}w" for x in s.get("subscriptions", [])) or "none"
    prefs = s.get("preferences", {})
    hh = s.get("household", {})
    store = s.get("home_store") or {}
    return "\n".join([
        f"# Shopper profile {s['shopper_id']}: {s['first_name']} {s['last_name'][:1]}.",
        f"Source: Databricks daily export {s['export_date']}, model {m.get('model_version')}.",
        "",
        "## Shopper",
        f"{s['first_name']} shops from {s['address']['city']}, {s['address']['state']} and is a {hh.get('type_name', 'household').lower()} "
        f"of {hh.get('size', 1)}" + (f" with {', '.join(hh['pets'])}" if hh.get("pets") else "") + ". "
        f"Home store: {store.get('name')}. Orders ship from {C.FULFILLMENT_CENTERS[s['delivery']['fulfillment_center']]['name']}. "
        f"Default delivery: {s['delivery'].get('default_option')}. Delivery notes: {s['delivery'].get('notes') or 'none'}. "
        f"Connectivity: {s.get('engagement', {}).get('connectivity_note')}.",
        "",
        "## Membership and wallet",
        f"{s['membership']['tier_name']} member since {s['membership'].get('member_since')} ({s['membership']['discount_pct']}% off list), "
        f"renews {s['membership'].get('renewal_date') or 'n/a'}. Store credit ${float(w.get('store_credit_usd', 0)):,.2f}, "
        f"{int(w.get('points', 0)):,} points. Pays with {w.get('payment_method')}.",
        "",
        "## Orders and replenishment",
        f"Spend last 12 months ${s.get('spend', {}).get('last_12mo_usd', 0):,.0f} over {s.get('spend', {}).get('orders_last_12mo', 0)} orders, "
        f"average order ${s.get('spend', {}).get('avg_order_usd', 0):,.0f}. Recent orders: {hist_txt}.",
        f"Goes through: {rep_txt}.",
        f"Subscribe & Save: {subs}.",
        "",
        "## Preferences",
        f"Wants more of: {', '.join(C.CATEGORIES[c]['name'] for c in prefs.get('favorite_categories', []) if c in C.CATEGORIES) or 'no favorites set'}. "
        f"Hidden: {', '.join(C.CATEGORIES[c]['name'] for c in prefs.get('avoid_categories', []) if c in C.CATEGORIES) or 'nothing'}. "
        f"Substitutions {'allowed' if prefs.get('allow_substitutions', True) else 'not allowed'}.",
        "",
        "## Recommendations",
        f"Segment: {m.get('segment')}. Next best offer: {m.get('next_best_offer')}. Churn risk {m.get('churn_risk')}. "
        f"Predicted basket ${m.get('predicted_basket_usd', 0):,.0f}. Smart cart: {cart or 'nothing due'}. "
        f"Picks: {picks}. Price watch: {watch}.",
        "Personal offers: " + ("; ".join(f"{o['title']} ({o['reason']}, driver: {o.get('driver')}, expires {o.get('expires')})"
                                         for o in m.get("offers", [])) or "none") + ".",
    ])
