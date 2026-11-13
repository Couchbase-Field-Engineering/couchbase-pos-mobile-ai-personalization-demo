"""The ShopSmart Café & Kitchen menu: what the drive-thru lane and the phone
line sell made to order, next to the retail catalog in catalog.py.

Menu items are not in the Databricks export or in `catalog.PRODUCTS` (they
have no fulfillment-center stock and no replenishment model), so the nightly
generator and the phone app's catalog are untouched. They are priced by
pos.py with the same adjustment arithmetic the rules engine uses: member
tier, the promotion for the hour, a personal offer, a combo, capped.

ShopSmart is fictional; so is the menu.
"""
from __future__ import annotations

import hashlib

SIZES = ["small", "medium", "large"]

# Modifiers a guest can say. `pre` = usually said before the item ("iced
# oat-milk latte"), otherwise after it ("... with an extra shot", "no cheese").
MODIFIERS = {
    "oat_milk": {"label": "oat milk", "price": 0.70, "say": ["oat milk", "oatmilk", "oat"], "pre": True},
    "almond_milk": {"label": "almond milk", "price": 0.70, "say": ["almond milk", "almond"], "pre": True},
    "extra_shot": {"label": "extra shot", "price": 0.90, "say": ["extra shot", "double shot", "extra espresso", "add a shot"]},
    "vanilla": {"label": "vanilla", "price": 0.50, "say": ["vanilla"], "pre": True},
    "decaf": {"label": "decaf", "price": 0.0, "say": ["decaf", "decaffeinated"], "pre": True},
    "iced": {"label": "iced", "price": 0.0, "say": ["iced", "on ice"], "pre": True},
    "no_cheese": {"label": "no cheese", "price": 0.0, "say": ["no cheese", "without cheese", "hold the cheese"]},
    "no_bacon": {"label": "no bacon", "price": 0.0, "say": ["no bacon", "without bacon", "hold the bacon"]},
    "no_mayo": {"label": "no mayo", "price": 0.0, "say": ["no mayo", "without mayo", "hold the mayo", "no mayonnaise"]},
    "no_pickles": {"label": "no pickles", "price": 0.0, "say": ["no pickles", "without pickles", "hold the pickles"]},
    "no_tomato": {"label": "no tomato", "price": 0.0, "say": ["no tomato", "without tomato", "no tomatoes"]},
    "add_avocado": {"label": "avocado", "price": 1.25, "say": ["add avocado", "with avocado", "avocado"]},
    "add_cheese": {"label": "cheese", "price": 0.60, "say": ["add cheese", "with cheese", "extra cheese"]},
    "extra_egg": {"label": "extra egg", "price": 1.00, "say": ["extra egg", "double egg", "add an egg"]},
    "spicy": {"label": "spicy", "price": 0.0, "say": ["spicy", "make it spicy"], "pre": True},
    "salsa": {"label": "salsa", "price": 0.0, "say": ["salsa", "with salsa", "add salsa"]},
}

_COFFEE_MODS = ["oat_milk", "almond_milk", "extra_shot", "vanilla", "decaf", "iced"]

# sku, name, what a guest calls it, price (one price, or one per size), search
# tags (the hour's promotions match on these, like retail), allowed
# modifiers, seconds on the line, and the group combos are built from.
MENU = [
    {"sku": "CF-1001", "name": "Latte", "say": ["latte", "lattes", "cafe latte"], "sizes": {"small": 3.95, "medium": 4.65, "large": 5.25},
     "tags": ["coffee", "breakfast", "espresso"], "mods": _COFFEE_MODS, "prep_seconds": 75, "group": "coffee"},
    {"sku": "CF-1002", "name": "Drip Coffee", "say": ["drip coffee", "regular coffee", "black coffee", "coffee", "coffees"],
     "sizes": {"small": 1.95, "medium": 2.45, "large": 2.85}, "tags": ["coffee", "breakfast"],
     "mods": ["decaf", "oat_milk", "almond_milk", "vanilla"], "prep_seconds": 20, "group": "coffee"},
    {"sku": "CF-1003", "name": "Cold Brew", "say": ["cold brew", "cold brews", "iced coffee"], "sizes": {"small": 3.45, "medium": 3.95, "large": 4.45},
     "tags": ["coffee", "breakfast"], "mods": ["oat_milk", "almond_milk", "vanilla", "extra_shot"], "prep_seconds": 25, "group": "coffee"},
    {"sku": "CF-1004", "name": "Iced Tea", "say": ["iced tea", "iced teas", "tea"], "sizes": {"small": 2.25, "medium": 2.65, "large": 2.95},
     "tags": ["drinks"], "mods": [], "prep_seconds": 15, "group": "drink"},
    {"sku": "CF-1005", "name": "Fountain Lemonade", "say": ["lemonade", "lemonades", "fountain drink", "soda"],
     "sizes": {"small": 1.89, "medium": 2.19, "large": 2.49}, "tags": ["drinks"], "mods": [], "prep_seconds": 15, "group": "drink"},
    {"sku": "CF-2001", "name": "Bacon, Egg & Cheese Sandwich",
     "say": ["bacon egg and cheese sandwich", "bacon egg and cheese", "bacon egg cheese", "breakfast sandwich", "breakfast sandwiches", "egg sandwich"],
     "price": 4.95, "tags": ["breakfast", "sandwich"], "mods": ["no_cheese", "no_bacon", "add_avocado", "extra_egg"],
     "prep_seconds": 150, "group": "breakfast_entree"},
    {"sku": "CF-2002", "name": "Breakfast Burrito", "say": ["breakfast burrito", "breakfast burritos", "burrito", "burritos"], "price": 5.45,
     "tags": ["breakfast"], "mods": ["no_cheese", "add_avocado", "salsa", "spicy", "extra_egg"], "prep_seconds": 170, "group": "breakfast_entree"},
    {"sku": "CF-2003", "name": "Hash Browns", "say": ["hash browns", "hash brown", "hashbrowns", "hashbrown"], "price": 1.95,
     "tags": ["breakfast", "sides"], "mods": [], "prep_seconds": 60, "group": "breakfast_side", "kitchen_par": 24},
    {"sku": "CF-2004", "name": "Blueberry Muffin", "say": ["blueberry muffin", "blueberry muffins", "muffin", "muffins"], "price": 2.75,
     "tags": ["breakfast", "bakery"], "mods": [], "prep_seconds": 10, "group": "bakery", "kitchen_par": 12},
    {"sku": "CF-3001", "name": "Crispy Chicken Sandwich", "say": ["crispy chicken sandwich", "chicken sandwich", "chicken sandwiches", "crispy chicken"],
     "price": 6.95, "tags": ["lunch", "sandwich"], "mods": ["no_pickles", "no_mayo", "spicy", "add_cheese"], "prep_seconds": 210, "group": "lunch_entree"},
    {"sku": "CF-3002", "name": "Turkey Club", "say": ["turkey club", "turkey clubs", "club sandwich", "turkey sandwich"], "price": 7.45,
     "tags": ["lunch", "sandwich"], "mods": ["no_tomato", "no_mayo", "no_bacon", "add_avocado"], "prep_seconds": 160, "group": "lunch_entree"},
    {"sku": "CF-3003", "name": "Fries", "say": ["fries", "french fries", "fry"], "sizes": {"small": 2.45, "medium": 2.95, "large": 3.45},
     "tags": ["lunch", "sides"], "mods": [], "prep_seconds": 90, "group": "lunch_side"},
    {"sku": "CF-3004", "name": "Side Salad", "say": ["side salad", "side salads", "salad"], "price": 3.25, "tags": ["lunch", "sides"],
     "mods": [], "prep_seconds": 40, "group": "lunch_side"},
    {"sku": "CF-3005", "name": "Kids Meal", "say": ["kids meal", "kids meals", "kid's meal", "kid meal", "kids' meal"], "price": 5.25,
     "tags": ["kids", "lunch"], "mods": ["no_cheese"], "prep_seconds": 150, "group": "kids"},
    {"sku": "CF-4001", "name": "Chocolate Chip Cookie", "say": ["chocolate chip cookie", "chocolate chip cookies", "cookie", "cookies"], "price": 1.75,
     "tags": ["bakery"], "mods": [], "prep_seconds": 5, "group": "bakery", "kitchen_par": 18},
]
MENU_BY_SKU = {m["sku"]: m for m in MENU}
DEFAULT_SIZE = "medium"

# Combos: one item from every group in the cart takes pct_off off each of those lines.
COMBOS = [
    {"id": "breakfast-combo", "name": "Breakfast combo", "groups": ["breakfast_entree", "breakfast_side", "coffee"], "pct_off": 12,
     "reason": "Sandwich or burrito, hash browns and a coffee"},
    {"id": "lunch-combo", "name": "Lunch combo", "groups": ["lunch_entree", "lunch_side", "drink"], "pct_off": 10,
     "reason": "Sandwich, a side and a drink"},
]
# What the lane suggests to complete a combo, per missing group.
COMBO_FILL = {"breakfast_side": "CF-2003", "coffee": "CF-1002", "breakfast_entree": "CF-2001", "lunch_side": "CF-3003",
              "drink": "CF-1004", "lunch_entree": "CF-3001"}

# The kitchen holds a few prepared items. Twice par (overbaked) is marked down
# and becomes what the lane suggests; zero is 86'd.
KITCHEN_OVERSTOCK_RATIO = 2.0
KITCHEN_OVERSTOCK_PCT = 15

# What the three demo shoppers always order. Everyone else's usual is derived
# from their shopper id; in production it is one more column of the nightly export.
_USUALS = {
    "S00101001": [{"sku": "CF-1001", "size": "large", "mods": ["oat_milk"], "qty": 1}, {"sku": "CF-2001", "mods": [], "qty": 1}],
    "S00101002": [{"sku": "CF-1002", "size": "medium", "mods": [], "qty": 1}, {"sku": "CF-2002", "mods": ["salsa"], "qty": 1}],
    "S00101003": [{"sku": "CF-1003", "size": "large", "mods": ["vanilla"], "qty": 1}, {"sku": "CF-3005", "mods": [], "qty": 2}],
}
_USUAL_POOL = [
    [{"sku": "CF-1002", "size": "large", "mods": [], "qty": 1}],
    [{"sku": "CF-1001", "size": "medium", "mods": [], "qty": 1}, {"sku": "CF-2004", "mods": [], "qty": 1}],
    [{"sku": "CF-1003", "size": "medium", "mods": [], "qty": 1}, {"sku": "CF-2001", "mods": [], "qty": 1}],
    [{"sku": "CF-3001", "mods": [], "qty": 1}, {"sku": "CF-3003", "size": "medium", "mods": [], "qty": 1}],
    [{"sku": "CF-1004", "size": "large", "mods": [], "qty": 1}, {"sku": "CF-3002", "mods": [], "qty": 1}],
]


def usual_for(shopper_id: str) -> list[dict]:
    if shopper_id in _USUALS:
        return [dict(l, mods=list(l["mods"])) for l in _USUALS[shopper_id]]
    h = int(hashlib.sha1(f"usual|{shopper_id}".encode()).hexdigest()[:8], 16)
    return [dict(l, mods=list(l["mods"])) for l in _USUAL_POOL[h % len(_USUAL_POOL)]]


def is_menu(sku: str) -> bool:
    return sku in MENU_BY_SKU


def normalize_line(line: dict) -> dict:
    """A menu line with a valid size and only the modifiers the item takes."""
    m = MENU_BY_SKU[line["sku"]]
    size = None
    if "sizes" in m:
        size = line.get("size") if line.get("size") in m["sizes"] else DEFAULT_SIZE
    mods = sorted({x for x in (line.get("mods") or []) if x in m["mods"]})
    return {"sku": m["sku"], "qty": int(line.get("qty") or 1), "size": size, "mods": mods}


def base_price(sku: str, size: str | None, mods: list[str]) -> float:
    m = MENU_BY_SKU[sku]
    price = m["sizes"][size or DEFAULT_SIZE] if "sizes" in m else m["price"]
    return round(price + sum(MODIFIERS[x]["price"] for x in mods), 2)


def describe(sku: str, size: str | None = None, mods: list[str] | None = None) -> str:
    """How the lane says and prints a line: 'large oat milk Latte'."""
    m = MENU_BY_SKU[sku]
    mods = mods or []
    pre = " ".join(MODIFIERS[x]["label"] for x in mods if MODIFIERS[x].get("pre"))
    post = ", ".join(MODIFIERS[x]["label"] for x in mods if not MODIFIERS[x].get("pre"))
    head = " ".join(x for x in [size if "sizes" in m else None, pre, m["name"]] if x)
    return head + (f" ({post})" if post else "")


def line_key(sku: str, size: str | None, mods: list[str]) -> str:
    return "|".join([sku, size or "", ",".join(sorted(mods))])


def public_menu() -> dict:
    return {"items": [{"sku": m["sku"], "name": m["name"], "group": m["group"], "tags": m["tags"],
                       "sizes": m.get("sizes"), "price": m.get("price"), "mods": m["mods"]} for m in MENU],
            "modifiers": {k: {"label": v["label"], "price": v["price"]} for k, v in MODIFIERS.items()},
            "combos": COMBOS}
