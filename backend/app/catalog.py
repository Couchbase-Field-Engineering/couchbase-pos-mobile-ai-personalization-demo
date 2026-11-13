"""Reference catalog for the demo: what Couchbase ShopSmart sells, member
pricing tiers, delivery options, the fulfillment centers that ship orders and
the stores shoppers pick up from.

This is the single source of truth shared by the Databricks mock-dump
generator (databricks/generate_mock_dump.py imports it), the backend's
recommendation engine and the MCP tools. ShopSmart is a fictional retailer:
products, brands, prices, stores and competitor names are invented for the
demo.
"""
from __future__ import annotations

import hashlib

# --------------------------------------------------------------------------
# Product categories. `affinity` names the model score that drives how
# strongly a category is recommended to a shopper.
# --------------------------------------------------------------------------
CATEGORIES = {
    "pantry": {"name": "Pantry & grocery", "affinity": "pantry_affinity", "icon": "basket"},
    "household": {"name": "Household essentials", "affinity": "household_affinity", "icon": "home"},
    "personal": {"name": "Personal care", "affinity": "personal_affinity", "icon": "water"},
    "baby": {"name": "Baby", "affinity": "baby_affinity", "icon": "happy"},
    "pets": {"name": "Pets", "affinity": "pets_affinity", "icon": "paw"},
    "kitchen": {"name": "Home & kitchen", "affinity": "kitchen_affinity", "icon": "restaurant"},
    "electronics": {"name": "Electronics", "affinity": "electronics_affinity", "icon": "headset"},
    "toys": {"name": "Toys & games", "affinity": "toys_affinity", "icon": "game-controller"},
    "outdoor": {"name": "Sports & outdoors", "affinity": "outdoor_affinity", "icon": "bicycle"},
}

# SKU, name, category, unit of measure, list price (USD), pack size, search
# tags, and whether it is a consumable the replenishment model tracks.
# `limit_per_household` marks a high-demand item with a purchase limit.
PRODUCTS = [
    # pantry & grocery
    {"sku": "SS-110245", "name": "Morning Ridge Medium Roast Coffee Pods, 72-count", "category": "pantry", "uom": "box",
     "list_price": 34.99, "pack": "72 pods", "tags": ["coffee", "pods", "breakfast"], "consumable": True},
    {"sku": "SS-110260", "name": "Morning Ridge Medium Roast Coffee Pods, 120-count value box", "category": "pantry", "uom": "box",
     "list_price": 52.99, "pack": "120 pods", "tags": ["coffee", "pods", "breakfast", "value size"], "consumable": True},
    {"sku": "SS-110388", "name": "Golden Field Rolled Oats, 42 oz, 2-pack", "category": "pantry", "uom": "pack",
     "list_price": 9.49, "pack": "2 canisters", "tags": ["breakfast", "oatmeal"], "consumable": True},
    {"sku": "SS-110512", "name": "Casa Verde Extra Virgin Olive Oil, 1 L", "category": "pantry", "uom": "bottle",
     "list_price": 14.99, "pack": "1 L", "tags": ["cooking", "oil"], "consumable": True},
    {"sku": "SS-110630", "name": "Harvest Table Penne Pasta, 16 oz, 6-pack", "category": "pantry", "uom": "pack",
     "list_price": 8.99, "pack": "6 boxes", "tags": ["pasta", "dinner"], "consumable": True},
    {"sku": "SS-110777", "name": "Spring Peak Sparkling Water, lime, 24 cans", "category": "pantry", "uom": "case",
     "list_price": 11.99, "pack": "24 cans", "tags": ["drinks", "sparkling", "water"], "consumable": True},
    {"sku": "SS-110903", "name": "Trail Mix Snack Packs, 24-count", "category": "pantry", "uom": "box",
     "list_price": 16.49, "pack": "24 packs", "tags": ["snacks", "lunchbox"], "consumable": True},
    {"sku": "SS-111046", "name": "Little Orchard Fruit Pouches, variety, 18-count", "category": "pantry", "uom": "box",
     "list_price": 13.99, "pack": "18 pouches", "tags": ["snacks", "kids", "lunchbox"], "consumable": True},
    # household essentials
    {"sku": "SS-220134", "name": "Everfresh Paper Towels, select-a-size, 12 mega rolls", "category": "household", "uom": "pack",
     "list_price": 24.99, "pack": "12 rolls", "tags": ["paper", "kitchen", "cleaning"], "consumable": True},
    {"sku": "SS-220268", "name": "CloudSoft Bath Tissue, 30 family rolls", "category": "household", "uom": "pack",
     "list_price": 27.99, "pack": "30 rolls", "tags": ["paper", "bathroom"], "consumable": True},
    {"sku": "SS-220391", "name": "ClearTide Laundry Detergent, free & clear, 128 loads", "category": "household", "uom": "jug",
     "list_price": 19.99, "pack": "170 fl oz", "tags": ["laundry", "unscented"], "consumable": True},
    {"sku": "SS-220455", "name": "Lemon Grove Dish Soap, 28 oz, 3-pack", "category": "household", "uom": "pack",
     "list_price": 10.49, "pack": "3 bottles", "tags": ["kitchen", "dishes", "cleaning"], "consumable": True},
    {"sku": "SS-220579", "name": "SparkWash Dishwasher Pods, 78-count", "category": "household", "uom": "tub",
     "list_price": 21.99, "pack": "78 pods", "tags": ["kitchen", "dishes", "dishwasher"], "consumable": True},
    {"sku": "SS-220642", "name": "ToughLine Tall Kitchen Trash Bags, 13 gal, 120-count", "category": "household", "uom": "box",
     "list_price": 18.99, "pack": "120 bags", "tags": ["trash", "kitchen"], "consumable": True},
    {"sku": "SS-220788", "name": "PureWipe Disinfecting Wipes, 80-count, 3-pack", "category": "household", "uom": "pack",
     "list_price": 12.99, "pack": "240 wipes", "tags": ["cleaning", "wipes"], "consumable": True},
    # personal care
    {"sku": "SS-330117", "name": "Brightline Whitening Toothpaste, 6.4 oz, 3-pack", "category": "personal", "uom": "pack",
     "list_price": 11.49, "pack": "3 tubes", "tags": ["dental", "bathroom"], "consumable": True},
    {"sku": "SS-330254", "name": "Meadow & Mint Shampoo, 28 oz", "category": "personal", "uom": "bottle",
     "list_price": 9.99, "pack": "28 fl oz", "tags": ["hair", "shower"], "consumable": True},
    {"sku": "SS-330396", "name": "Sea Cotton Body Wash, 32 oz, 2-pack", "category": "personal", "uom": "pack",
     "list_price": 13.49, "pack": "2 bottles", "tags": ["shower", "soap"], "consumable": True},
    {"sku": "SS-330431", "name": "SolGuard Mineral Sunscreen SPF 50, 6 oz", "category": "personal", "uom": "tube",
     "list_price": 14.99, "pack": "6 oz", "tags": ["sun", "summer", "outdoor"], "consumable": True},
    {"sku": "SS-330570", "name": "Daily Balance Multivitamin Gummies, 150-count", "category": "personal", "uom": "bottle",
     "list_price": 17.99, "pack": "150 gummies", "tags": ["vitamins", "wellness"], "consumable": True},
    # baby
    {"sku": "SS-440129", "name": "Snugglebug Diapers, size 4, 150-count", "category": "baby", "uom": "box",
     "list_price": 44.99, "pack": "150 diapers", "tags": ["diapers", "toddler"], "consumable": True},
    {"sku": "SS-440145", "name": "Snugglebug Diapers, size 4, 222-count value box", "category": "baby", "uom": "box",
     "list_price": 61.99, "pack": "222 diapers", "tags": ["diapers", "toddler", "value size"], "consumable": True},
    {"sku": "SS-440263", "name": "Snugglebug Sensitive Baby Wipes, 720-count", "category": "baby", "uom": "case",
     "list_price": 19.99, "pack": "9 packs of 80", "tags": ["wipes", "unscented"], "consumable": True},
    {"sku": "SS-440377", "name": "First Steps Toddler Formula, 30 oz", "category": "baby", "uom": "can",
     "list_price": 32.99, "pack": "30 oz", "tags": ["formula", "toddler"], "consumable": True},
    {"sku": "SS-440485", "name": "Dreamnest Convertible Car Seat", "category": "baby", "uom": "each",
     "list_price": 189.99, "pack": "1 seat", "tags": ["car seat", "travel", "safety"], "consumable": False},
    # pets
    {"sku": "SS-550118", "name": "Trailhound Adult Dog Food, chicken & rice, 30 lb", "category": "pets", "uom": "bag",
     "list_price": 46.99, "pack": "30 lb", "tags": ["dog", "food"], "consumable": True},
    {"sku": "SS-550133", "name": "Trailhound Adult Dog Food, chicken & rice, 44 lb value bag", "category": "pets", "uom": "bag",
     "list_price": 62.99, "pack": "44 lb", "tags": ["dog", "food", "value size"], "consumable": True},
    {"sku": "SS-550246", "name": "Whiskerwell Indoor Cat Food, salmon, 16 lb", "category": "pets", "uom": "bag",
     "list_price": 31.99, "pack": "16 lb", "tags": ["cat", "food"], "consumable": True},
    {"sku": "SS-550359", "name": "FreshStep Clumping Cat Litter, unscented, 40 lb", "category": "pets", "uom": "box",
     "list_price": 22.99, "pack": "40 lb", "tags": ["cat", "litter", "unscented"], "consumable": True},
    {"sku": "SS-550472", "name": "Trailhound Dental Chews, 36-count", "category": "pets", "uom": "bag",
     "list_price": 24.49, "pack": "36 chews", "tags": ["dog", "treats"], "consumable": True},
    {"sku": "SS-550590", "name": "Orthopedic Pet Bed, large, washable cover", "category": "pets", "uom": "each",
     "list_price": 59.99, "pack": "1 bed", "tags": ["dog", "cat", "bed"], "consumable": False},
    # home & kitchen
    {"sku": "SS-660102", "name": "Hearthside 5.5 qt Stand Mixer, graphite", "category": "kitchen", "uom": "each",
     "list_price": 279.99, "pack": "1 mixer", "tags": ["baking", "appliance", "mixer"], "consumable": False},
    {"sku": "SS-660237", "name": "CrispWave 6 qt Air Fryer", "category": "kitchen", "uom": "each",
     "list_price": 119.99, "pack": "1 air fryer", "tags": ["appliance", "cooking"], "consumable": False},
    {"sku": "SS-660250", "name": "CrispWave 8 qt Dual-Basket Air Fryer", "category": "kitchen", "uom": "each",
     "list_price": 159.99, "pack": "1 air fryer", "tags": ["appliance", "cooking", "premium"], "consumable": False},
    {"sku": "SS-660341", "name": "Morning Ridge Single-Serve Coffee Maker", "category": "kitchen", "uom": "each",
     "list_price": 89.99, "pack": "1 brewer", "tags": ["coffee", "appliance", "pods"], "consumable": False},
    {"sku": "SS-660468", "name": "Forge & Field 10-piece Nonstick Cookware Set", "category": "kitchen", "uom": "set",
     "list_price": 149.99, "pack": "10 pieces", "tags": ["cooking", "pans"], "consumable": False},
    {"sku": "SS-660573", "name": "StackRight Glass Food Storage, 18-piece", "category": "kitchen", "uom": "set",
     "list_price": 34.99, "pack": "9 containers + lids", "tags": ["storage", "meal prep", "lunchbox"], "consumable": False},
    {"sku": "SS-660689", "name": "Linen Loft Bath Towels, 6-piece set", "category": "kitchen", "uom": "set",
     "list_price": 39.99, "pack": "6 towels", "tags": ["bath", "home"], "consumable": False},
    # electronics
    {"sku": "SS-770114", "name": "Aria Wireless Earbuds with noise cancelling", "category": "electronics", "uom": "each",
     "list_price": 129.99, "pack": "1 pair", "tags": ["audio", "headphones", "commute"], "consumable": False},
    {"sku": "SS-770128", "name": "Aria Pro Wireless Earbuds with spatial audio", "category": "electronics", "uom": "each",
     "list_price": 179.99, "pack": "1 pair", "tags": ["audio", "headphones", "commute", "premium"], "consumable": False},
    {"sku": "SS-770229", "name": "Nova X Handheld Game Console", "category": "electronics", "uom": "each",
     "list_price": 349.99, "pack": "1 console", "tags": ["gaming", "console"], "consumable": False, "limit_per_household": 1},
    {"sku": "SS-770336", "name": "Lumen 10 in Tablet, 128 GB", "category": "electronics", "uom": "each",
     "list_price": 219.99, "pack": "1 tablet", "tags": ["tablet", "kids", "streaming"], "consumable": False},
    {"sku": "SS-770447", "name": "PowerLoop 3-in-1 Wireless Charger", "category": "electronics", "uom": "each",
     "list_price": 39.99, "pack": "1 charger", "tags": ["charging", "phone"], "consumable": False},
    {"sku": "SS-770552", "name": "HomeGlow Smart Bulbs, 4-pack", "category": "electronics", "uom": "pack",
     "list_price": 29.99, "pack": "4 bulbs", "tags": ["smart home", "lighting"], "consumable": False},
    # toys & games
    {"sku": "SS-880121", "name": "BrickWorks Creative Building Set, 900 pieces", "category": "toys", "uom": "each",
     "list_price": 49.99, "pack": "900 pieces", "tags": ["building", "kids", "gift"], "consumable": False},
    {"sku": "SS-880243", "name": "Family Game Night Board Game Bundle", "category": "toys", "uom": "set",
     "list_price": 34.99, "pack": "3 games", "tags": ["board games", "family", "gift"], "consumable": False},
    {"sku": "SS-880365", "name": "Washable Art Supplies Mega Kit", "category": "toys", "uom": "each",
     "list_price": 24.99, "pack": "150 pieces", "tags": ["art", "kids", "craft"], "consumable": False},
    # sports & outdoors
    {"sku": "SS-990107", "name": "SummitFlow Insulated Water Bottle, 32 oz", "category": "outdoor", "uom": "each",
     "list_price": 27.99, "pack": "1 bottle", "tags": ["hydration", "gym", "hiking"], "consumable": False},
    {"sku": "SS-990218", "name": "FlexCore Yoga Mat, 6 mm", "category": "outdoor", "uom": "each",
     "list_price": 32.99, "pack": "1 mat", "tags": ["yoga", "fitness", "gym"], "consumable": False},
    {"sku": "SS-990334", "name": "PeakFuel Protein Bars, chocolate, 18-count", "category": "outdoor", "uom": "box",
     "list_price": 26.99, "pack": "18 bars", "tags": ["protein", "snacks", "gym"], "consumable": True},
    {"sku": "SS-990451", "name": "TrailLite 2-Person Backpacking Tent", "category": "outdoor", "uom": "each",
     "list_price": 159.99, "pack": "1 tent", "tags": ["camping", "hiking"], "consumable": False},
]
PRODUCT_BY_SKU = {p["sku"]: p for p in PRODUCTS}

# --------------------------------------------------------------------------
# Merchandising: what goes with what, what the step-up is, and which items
# sell as a bundle. In production these come out of market-basket analysis in
# Databricks (the export's `_pricing_signals.json` carries them); the copies
# here are what the mock generator writes and what the engine falls back to.
# --------------------------------------------------------------------------
# Cross-sell: sku in the cart -> [(sku to suggest, why)]
PAIRS_WITH = {
    "SS-110245": [("SS-660341", "Brews the pods in your cart")],
    "SS-110260": [("SS-660341", "Brews the pods in your cart")],
    "SS-660341": [("SS-110245", "Pods for your new brewer")],
    "SS-440129": [("SS-440263", "Most parents add wipes"), ("SS-111046", "Snack pouches for the same age")],
    "SS-440145": [("SS-440263", "Most parents add wipes")],
    "SS-440263": [("SS-440129", "Diapers usually run out with the wipes")],
    "SS-550118": [("SS-550472", "Dental chews, bought with this food 6 times in 10"), ("SS-550590", "Bed for the same dog")],
    "SS-550133": [("SS-550472", "Dental chews, bought with this food 6 times in 10")],
    "SS-550246": [("SS-550359", "Litter is bought with this food")],
    "SS-550359": [("SS-550246", "Food is bought with this litter")],
    "SS-660102": [("SS-660573", "Store what you bake"), ("SS-110388", "Oats for the first batch")],
    "SS-660237": [("SS-110512", "Olive oil for the air fryer"), ("SS-660573", "Containers for meal prep")],
    "SS-660250": [("SS-110512", "Olive oil for the air fryer"), ("SS-660573", "Containers for meal prep")],
    "SS-770229": [("SS-770447", "Charges the console and your phone"), ("SS-770114", "Earbuds for handheld play")],
    "SS-770336": [("SS-770447", "Wireless charger for the tablet"), ("SS-770114", "Earbuds for streaming")],
    "SS-880243": [("SS-110903", "Snacks for game night"), ("SS-110777", "Drinks for game night")],
    "SS-990218": [("SS-990107", "Water bottle for the studio"), ("SS-990334", "Protein bars for after")],
    "SS-990451": [("SS-990107", "Water bottle for the trail"), ("SS-330431", "Sunscreen for the trail")],
    "SS-220579": [("SS-220455", "Dish soap for what the dishwasher can't take")],
    "SS-110630": [("SS-110512", "Olive oil for the pasta")],
    "SS-220391": [("SS-220788", "Wipes are bought with detergent")],
    "SS-220642": [("SS-220134", "Paper towels are bought with trash bags")],
}
# Upsell: sku -> (the step-up, why it's worth it)
UPGRADES = {
    "SS-110245": ("SS-110260", "Value box: 44 cents a pod instead of 49"),
    "SS-440129": ("SS-440145", "Value box: 28 cents a diaper instead of 30"),
    "SS-550118": ("SS-550133", "Value bag: $1.43 a pound instead of $1.57"),
    "SS-770114": ("SS-770128", "Pro model: spatial audio and 8 more hours of battery"),
    "SS-660237": ("SS-660250", "Two baskets: cook a main and a side at once"),
}
# Bundles: every item in the cart together takes pct_off off each of them.
BUNDLES = [
    {"id": "coffee-starter", "name": "Coffee starter", "skus": ["SS-660341", "SS-110245"], "pct_off": 15,
     "reason": "Brewer and pods are bought together by 7 in 10 new coffee shoppers"},
    {"id": "baby-essentials", "name": "Baby essentials", "skus": ["SS-440129", "SS-440263"], "pct_off": 8,
     "reason": "Diapers and wipes, the most common pair in baby baskets"},
    {"id": "new-pup", "name": "New pup", "skus": ["SS-550118", "SS-550472", "SS-550590"], "pct_off": 12,
     "reason": "Food, chews and a bed for a new dog"},
    {"id": "game-night", "name": "Game night", "skus": ["SS-880243", "SS-110903", "SS-110777"], "pct_off": 10,
     "reason": "Games, snacks and drinks for a night in"},
    {"id": "meal-prep", "name": "Meal prep", "skus": ["SS-660237", "SS-660573", "SS-110512"], "pct_off": 10,
     "reason": "Air fryer, containers and olive oil"},
]

# Dynamic pricing rules. Percentages are off list and add to the member
# tier and multi-buy discounts, up to MAX_DISCOUNT_PCT.
MAX_DISCOUNT_PCT = 40
OVERSTOCK_TIERS = [(4.6, 15), (4.0, 10)]   # (on hand / normal stock level, pct off)
LOW_STOCK_RATIO = 0.15                     # at or below this share of the normal level: no promotions
DYNAMIC_BUNDLE_PCT = 12
# A competitor price within this share of our list price is matched automatically;
# a bigger gap is a price-match request that Customer Care approves.
AUTO_MATCH_MIN_PCT_OF_LIST = 92
# Promotions tied to the hours the business is quiet or busy (24h clock, end exclusive).
TIME_WINDOWS = [
    {"id": "morning-coffee", "label": "Morning coffee run", "start_hour": 6, "end_hour": 10, "pct_off": 10,
     "tags": ["coffee", "breakfast"], "reason": "Quiet delivery hours: breakfast items are 10% off until 10 am"},
    {"id": "afternoon-restock", "label": "Afternoon restock", "start_hour": 13, "end_hour": 16, "pct_off": 5,
     "categories": ["household"], "reason": "Off-peak for our drivers: household essentials are 5% off from 1 to 4 pm"},
    {"id": "skip-the-rush", "label": "Skip the rush", "start_hour": 17, "end_hour": 20, "pct_off": 5, "peak": True,
     "requires_option": "Store pickup", "reason": "Peak delivery hours: 5% off everything when you pick up in store from 5 to 8 pm"},
]
# The competitor price feed (market-wide, not per shopper): sku -> (competitor, their price).
MARKET_PRICES = {
    "SS-770114": ("Northfield Supply", 122.99),
    "SS-660468": ("Brightway Market", 139.99),
    "SS-220268": ("Oakline Stores", 26.49),
    "SS-990218": ("Northfield Supply", 26.99),   # too far below list to match automatically
}
# Demand the forecast expects to outrun supply: never promoted.
HIGH_DEMAND = ["SS-770229"]


def stock_level(sku: str) -> int:
    """The normal on-hand level a fulfillment center carries for an item."""
    return 60 if PRODUCT_BY_SKU[sku]["consumable"] else 14


# What each kind of household goes through month to month: sku -> typical
# monthly quantity. The generator derives replenishment from this, with noise.
HOUSEHOLD_TYPES = {
    "young_family": {"name": "Young family", "usage": {
        "SS-440129": 1.2, "SS-440263": 1, "SS-440377": 1.5, "SS-220134": 1, "SS-220268": 1, "SS-220391": 1,
        "SS-111046": 2, "SS-110245": 1, "SS-220788": 1, "SS-220642": 0.5, "SS-110903": 1}},
    "pet_household": {"name": "Pet household", "usage": {
        "SS-550118": 1, "SS-550472": 1, "SS-550359": 1, "SS-550246": 1, "SS-220134": 1, "SS-220642": 0.5,
        "SS-220788": 1, "SS-110245": 1, "SS-220391": 0.5}},
    "busy_professional": {"name": "Busy professional", "usage": {
        "SS-110245": 1.5, "SS-110777": 2, "SS-990334": 1.5, "SS-220579": 0.4, "SS-330254": 0.5, "SS-330396": 0.5,
        "SS-330117": 0.3, "SS-220268": 0.5, "SS-330570": 0.4}},
    "couple": {"name": "Couple", "usage": {
        "SS-110245": 1, "SS-110512": 0.5, "SS-110630": 1, "SS-220134": 0.5, "SS-220268": 0.5, "SS-220455": 0.4,
        "SS-220579": 0.5, "SS-330396": 0.5, "SS-110777": 1, "SS-220391": 0.4}},
    "large_family": {"name": "Large family", "usage": {
        "SS-220134": 2, "SS-220268": 2, "SS-220391": 1.5, "SS-220642": 1, "SS-220579": 1, "SS-110630": 2,
        "SS-110903": 2, "SS-111046": 2, "SS-110388": 1, "SS-330117": 1, "SS-330396": 1, "SS-110777": 2}},
    "empty_nesters": {"name": "Empty nesters", "usage": {
        "SS-110245": 1, "SS-110388": 0.5, "SS-110512": 0.5, "SS-220268": 0.5, "SS-220455": 0.3, "SS-330570": 0.5,
        "SS-330117": 0.3, "SS-220134": 0.5, "SS-550246": 0.5, "SS-550359": 0.5}},
    "student": {"name": "Student", "usage": {
        "SS-110630": 1, "SS-110777": 1, "SS-110903": 1, "SS-220268": 0.3, "SS-220391": 0.3, "SS-330254": 0.3,
        "SS-990334": 1, "SS-110245": 0.5}},
    "fitness_enthusiast": {"name": "Fitness enthusiast", "usage": {
        "SS-990334": 2, "SS-110777": 2, "SS-110388": 1, "SS-330570": 0.5, "SS-330396": 1, "SS-330431": 0.3,
        "SS-220391": 0.5, "SS-110512": 0.5}},
    "home_chef": {"name": "Home chef", "usage": {
        "SS-110512": 1, "SS-110630": 1.5, "SS-220455": 0.5, "SS-220579": 1, "SS-220134": 1, "SS-110245": 1,
        "SS-220642": 0.5, "SS-110388": 0.5}},
}

# --------------------------------------------------------------------------
# ShopSmart Rewards membership tiers (discount off list) and what comes with
# them.
# --------------------------------------------------------------------------
MEMBER_TIERS = [
    {"code": "MEMBER", "name": "Member", "discount_pct": 0, "points_per_usd": 1, "free_delivery_min_usd": 35,
     "blurb": "Free to join: 1 point per dollar and free delivery on orders over $35."},
    {"code": "PLUS", "name": "Plus", "discount_pct": 5, "points_per_usd": 2, "free_delivery_min_usd": 0,
     "blurb": "5% off every item, 2 points per dollar and free delivery on every order."},
    {"code": "PREMIER", "name": "Premier", "discount_pct": 10, "points_per_usd": 3, "free_delivery_min_usd": 0,
     "blurb": "10% off every item, 3 points per dollar, free same-day delivery and early access to limited releases."},
]
TIER_BY_CODE = {t["code"]: t for t in MEMBER_TIERS}

# Multi-buy savings on top of the member discount, by quantity on a line.
MULTI_BUY = [(6, 10), (3, 5)]  # (min qty, extra pct)

DELIVERY_OPTIONS = ["Same-day delivery", "2-day shipping", "Store pickup", "Scheduled delivery"]
PICKUP_OPTION = "Store pickup"
DELIVERY_FEE_USD = 5.99
TAX_RATE = 0.0625

# Price match: competitors ShopSmart matches, and the guardrails Customer
# Care applies before approving one.
COMPETITORS = ["Brightway Market", "Northfield Supply", "Oakline Stores"]
PRICE_MATCH_FLOOR_PCT = 70      # a matched price can't go below 70% of list
PRICE_MATCH_MAX_SAVING_USD = 150.0
GOODWILL_MAX_USD = 100.0
RETURN_WINDOW_DAYS = 30
PURCHASE_LIMIT_WINDOW_DAYS = 30

# Fulfillment centers and the states they ship to. Inventory is held per FC.
FULFILLMENT_CENTERS = {
    "FC-NE": {"name": "Northeast Fulfillment Center", "city": "Putnam, CT", "states": ["MA", "NH", "ME", "VT", "CT", "RI"]},
    "FC-NY": {"name": "Hudson Valley Fulfillment Center", "city": "Montgomery, NY", "states": ["NY", "NJ", "PA"]},
    "FC-MA": {"name": "Mid-Atlantic Fulfillment Center", "city": "Hagerstown, MD", "states": ["MD", "VA", "DE", "DC", "WV", "NC"]},
    "FC-SE": {"name": "Southeast Fulfillment Center", "city": "Lakeland, FL", "states": ["FL", "GA", "SC", "AL", "TN"]},
    "FC-SC": {"name": "South Central Fulfillment Center", "city": "Arlington, TX", "states": ["TX", "OK", "LA", "AR", "NM"]},
    "FC-MW": {"name": "Midwest Fulfillment Center", "city": "Terre Haute, IN", "states": ["IL", "IN", "OH", "MI", "WI", "MN", "MO", "KY"]},
    "FC-W": {"name": "West Fulfillment Center", "city": "Ontario, CA", "states": ["CA", "AZ", "NV", "OR", "WA", "CO", "UT"]},
}
STATE_TO_FC = {st: fc for fc, d in FULFILLMENT_CENTERS.items() for st in d["states"]}

REGIONS = {
    "R-BOS-NS": {"name": "Boston North Shore", "state": "MA", "cities": [("Salem", "MA", "01970"), ("Beverly", "MA", "01915"),
                 ("Gloucester", "MA", "01930"), ("Peabody", "MA", "01960"), ("Danvers", "MA", "01923"), ("Lynn", "MA", "01901"),
                 ("Marblehead", "MA", "01945"), ("Newburyport", "MA", "01950"), ("Ipswich", "MA", "01938")]},
    "R-NY-HV": {"name": "Hudson Valley", "state": "NY", "cities": [("Kingston", "NY", "12401"), ("Rhinebeck", "NY", "12572"),
                ("Catskill", "NY", "12414"), ("New Paltz", "NY", "12561"), ("Poughkeepsie", "NY", "12601"), ("Hudson", "NY", "12534"),
                ("Saugerties", "NY", "12477"), ("Woodstock", "NY", "12498")]},
    "R-TX-DFW": {"name": "Dallas-Fort Worth", "state": "TX", "cities": [("Dallas", "TX", "75201"), ("Fort Worth", "TX", "76102"),
                 ("Arlington", "TX", "76010"), ("Plano", "TX", "75074"), ("Irving", "TX", "75060"), ("Denton", "TX", "76201"),
                 ("Garland", "TX", "75040"), ("McKinney", "TX", "75069")]},
    "R-PA-PHL": {"name": "Philadelphia Suburbs", "state": "PA", "cities": [("King of Prussia", "PA", "19406"), ("Media", "PA", "19063"),
                 ("West Chester", "PA", "19380"), ("Doylestown", "PA", "18901"), ("Norristown", "PA", "19401")]},
    "R-FL-TPA": {"name": "Tampa Bay", "state": "FL", "cities": [("Tampa", "FL", "33602"), ("St. Petersburg", "FL", "33701"),
                 ("Clearwater", "FL", "33755"), ("Brandon", "FL", "33511"), ("Lakeland", "FL", "33801")]},
    "R-OH-COL": {"name": "Central Ohio", "state": "OH", "cities": [("Columbus", "OH", "43215"), ("Dublin", "OH", "43017"),
                 ("Westerville", "OH", "43081"), ("Newark", "OH", "43055"), ("Lancaster", "OH", "43130")]},
    "R-CA-SAC": {"name": "Sacramento Valley", "state": "CA", "cities": [("Sacramento", "CA", "95814"), ("Roseville", "CA", "95678"),
                 ("Davis", "CA", "95616"), ("Elk Grove", "CA", "95624"), ("Folsom", "CA", "95630")]},
    "R-NC-RDU": {"name": "Raleigh-Durham", "state": "NC", "cities": [("Raleigh", "NC", "27601"), ("Durham", "NC", "27701"),
                 ("Cary", "NC", "27511"), ("Chapel Hill", "NC", "27514"), ("Apex", "NC", "27502")]},
}

# Where a shopper is when the phone has no signal - the reality the app is built for.
CONNECTIVITY_NOTES = ["Subway commute - no signal in the tunnels", "Parking garage under the office - no signal",
                      "Shops in-store, deep in the aisles - weak signal", "Rural drive home - dead zone on the county road",
                      "Flies weekly - shops in airplane mode", "Basement apartment - spotty Wi-Fi",
                      "Good signal", "Good signal", "Good signal"]

STORE_HOURS = ["Daily 7am-10pm", "Mon-Sat 8am-10pm, Sun 9am-8pm", "Daily 8am-9pm", "Daily 6am-11pm"]
STORE_SERVICES = ["Order pickup", "Curbside pickup", "Returns desk", "Pharmacy", "Photo center", "Cafe"]
AISLE_BY_CATEGORY = {"pantry": "A", "household": "B", "personal": "C", "baby": "D", "pets": "E", "kitchen": "F",
                     "electronics": "G", "toys": "H", "outdoor": "J"}


def _h(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _stores() -> dict:
    """One store per city in every region. Deterministic, so the generator,
    the loader and the tests agree on ids without a store table in the export."""
    out = {}
    for ri, (rid, reg) in enumerate(REGIONS.items(), start=1):
        for ci, (city, state, zipc) in enumerate(reg["cities"], start=1):
            sid = f"ST-{ri:02d}{ci:02d}"
            h = _h("store", sid)
            out[sid] = {"store_id": sid, "name": f"ShopSmart {city}", "city": city, "state": state, "zip": zipc,
                        "street": f"{100 + h % 1800} {['Market St', 'Commerce Way', 'Main St', 'Retail Row', 'Plaza Dr'][h % 5]}",
                        "region_id": rid, "region_name": reg["name"], "hours": STORE_HOURS[h % len(STORE_HOURS)],
                        "phone": f"({200 + h % 700}) 555-{1000 + h % 9000:04d}"[:14],
                        "services": [s for i, s in enumerate(STORE_SERVICES) if i < 3 or (h >> i) & 1],
                        "fulfillment_center": STATE_TO_FC.get(state, "FC-MW")}
    return out


STORES = _stores()


def member_price(sku: str, tier_code: str, qty: int = 1) -> float:
    """Unit price for a shopper: list, less the member tier discount, less
    any multi-buy saving for the line quantity."""
    p = PRODUCT_BY_SKU[sku]
    pct = TIER_BY_CODE[tier_code]["discount_pct"]
    for min_qty, extra in MULTI_BUY:
        if qty >= min_qty:
            pct += extra
            break
    return round(p["list_price"] * (1 - pct / 100.0), 2)


def initial_inventory(fc: str) -> dict:
    """Deterministic on-hand units per SKU at a fulfillment center. A few
    SKUs are deliberately short so partial shipments and backorders show up."""
    out = {}
    for p in PRODUCTS:
        base = stock_level(p["sku"])
        out[p["sku"]] = base + _h(fc, p["sku"]) % (base * 3)
    # Hero shortages: the handheld console is tight everywhere, coffee pods are
    # nearly gone in the Northeast, dog food is out in the Hudson Valley.
    out["SS-770229"] = 2 + _h(fc, "short") % 3
    out["SS-330431"] = 300   # sunscreen in November: overstocked everywhere
    if fc == "FC-NE":
        out["SS-110245"] = 1
        out["SS-220134"] = 330   # a truckload of paper towels landed in the Northeast
    if fc == "FC-SC":
        out["SS-880121"] = 70    # building sets overstocked in Texas
    if fc == "FC-NY":
        out["SS-550118"] = 0
    return out


def fc_for_state(state: str) -> str:
    return STATE_TO_FC.get(state, "FC-MW")


def aisle_for(store_id: str, sku: str) -> str:
    p = PRODUCT_BY_SKU[sku]
    return f"{AISLE_BY_CATEGORY[p['category']]}{1 + _h(store_id, sku) % 14}"


def store_doc_body(store_id: str) -> dict:
    """What the phone holds about the shopper's home store: hours, services
    and the aisle of every product, so in-store lookups work with no signal."""
    s = STORES[store_id]
    return {**s, "aisles": {p["sku"]: aisle_for(store_id, p["sku"]) for p in PRODUCTS},
            "pickup_ready_minutes": 90 + _h(store_id, "pickup") % 60}


def public_catalog() -> dict:
    """What the mobile app syncs as the `catalog` document."""
    return {
        "categories": {k: v["name"] for k, v in CATEGORIES.items()},
        "category_icons": {k: v["icon"] for k, v in CATEGORIES.items()},
        "products": PRODUCTS,
        "member_tiers": MEMBER_TIERS,
        "multi_buy": [{"min_qty": q, "extra_pct": e} for q, e in MULTI_BUY],
        "delivery_options": DELIVERY_OPTIONS,
        "pickup_option": PICKUP_OPTION,
        "delivery_fee_usd": DELIVERY_FEE_USD,
        "tax_rate": TAX_RATE,
        "competitors": COMPETITORS,
        "price_match_floor_pct": PRICE_MATCH_FLOOR_PCT,
        "fulfillment_centers": {k: {"name": v["name"], "city": v["city"]} for k, v in FULFILLMENT_CENTERS.items()},
        "household_types": {k: v["name"] for k, v in HOUSEHOLD_TYPES.items()},
        "pairs_with": {k: [{"sku": s, "reason": r} for s, r in v] for k, v in PAIRS_WITH.items()},
        "upgrades": {k: {"sku": s, "reason": r} for k, (s, r) in UPGRADES.items()},
        "max_discount_pct": MAX_DISCOUNT_PCT,
    }
