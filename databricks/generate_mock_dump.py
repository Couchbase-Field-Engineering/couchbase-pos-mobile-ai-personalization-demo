#!/usr/bin/env python3
"""Generate a mock Databricks daily export of shopper personalization data.

Writes the exact layout a Databricks job produces with
`df.repartition(n).write.mode("overwrite").json(path)`:

    data/databricks-dump/dt=2026-11-13/
        part-00000-<uuid>-c000.json      # JSON Lines, one shopper per line
        part-00001-<uuid>-c000.json
        ...
        _SUCCESS
        _manifest.json                   # row/byte counts + schema version (written by the notebook)
        _pricing_signals.json            # competitor prices, demand forecast, promotion windows,
                                         # bundles and cross-sell affinities for the pricing engine

Each line is one Couchbase ShopSmart shopper joined with their membership,
wallet, household, home store, delivery defaults, preferences, the last
orders, month-to-month replenishment per SKU, and the scores the next-basket
model (shopsmart-next-basket-v2.3) produced in Databricks: per-category
affinity, a smart cart with a confidence per line, days-to-runout,
recommended items with a reason each, competitor price watches, churn risk
and the personal offers the loyalty model issued (a category or item
discount, bonus points), each with the reason it was chosen for this shopper.

ShopSmart is a fictional retailer; every shopper is synthetic.

Fully deterministic for a given --seed and --export-date, and standard
library only (runs anywhere Python 3.9+ does). Default target is 100 MiB.

    python databricks/generate_mock_dump.py                 # today, 100 MiB
    python databricks/generate_mock_dump.py --export-date 2026-11-13 --target-mb 100
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from app import catalog as C  # noqa: E402

SCHEMA_VERSION = "shopper_personalization_v1"
MODEL_VERSION = "shopsmart-next-basket-v2.3"
SOURCE_TABLE = "shopsmart_prod.customer360.shopper_personalization_daily"

FIRST = ["Olivia", "Liam", "Emma", "Noah", "Ava", "Elijah", "Sophia", "James", "Isabella", "Lucas", "Mia", "Mason",
         "Amelia", "Ethan", "Harper", "Logan", "Evelyn", "Aiden", "Abigail", "Jackson", "Emily", "Sebastian", "Ella",
         "Mateo", "Scarlett", "Daniel", "Grace", "Henry", "Chloe", "Owen", "Camila", "Wyatt", "Aria", "Carter", "Riley",
         "Jayden", "Nora", "Gabriel", "Lily", "Julian", "Zoey", "Leo", "Hannah", "David", "Layla", "Joseph", "Priya",
         "Arjun", "Mei", "Hiroshi", "Fatima", "Omar", "Sofia", "Diego", "Valentina", "Carlos", "Lucia", "Andre", "Aaliyah",
         "Marcus", "Keisha", "Tyrone", "Ngozi", "Kwame", "Siobhan", "Declan", "Ingrid", "Lars", "Anika", "Ravi"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
        "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young",
        "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker",
        "Hall", "Rivera", "Campbell", "Mitchell", "Carter", "Roberts", "Patel", "Shah", "Kim", "Chen", "Okafor",
        "O'Brien", "Kowalski", "Nakamura", "Fischer", "Rossi", "Bennett", "Natarajan", "Haddad", "Silva"]
STREETS = ["Main St", "Washington St", "Elm St", "Market St", "Broadway", "Church St", "Maple Ave", "Oak St", "Park Ave",
           "Mill Rd", "Union St", "Water St", "High St", "Center St", "Bridge St", "Front St", "Pleasant St", "School St"]
DELIVERY_NOTES = ["Leave at the side door", "Ring the bell - dog barks, that's fine", "Building lobby, concierge signs",
                  "Back porch, not the front steps", "Apartment 3B - buzz twice", "", "", "", ""]
SEGMENTS = ["Routine Replenishers", "Deal Seekers", "New Parents", "Pet Parents", "Weekend Stock-Up", "Lapsing Loyalists",
            "Premium Explorers", "New Members"]
PETS = {"pet_household": [["1 dog"], ["2 dogs"], ["1 cat"], ["1 dog", "1 cat"], ["2 cats"]]}
PAYMENTS = ["Visa ending in {}", "Mastercard ending in {}", "Amex ending in {}", "ShopSmart Card ending in {}"]
REC_REASONS = {
    "kitchen": ["Pairs with what you cook most", "Shoppers with your basket upgrade to this", "Viewed 3 times this month"],
    "electronics": ["Popular with shoppers like you", "Back in stock - you looked at it last week", "Goes with your recent order"],
    "toys": ["Top pick for families like yours", "Trending in your area this week"],
    "outdoor": ["Matches your weekend buys", "Popular in your area this season"],
    "pets": ["Pet parents with your basket add this", "Goes with what you buy for your pet"],
    "baby": ["Parents at this stage buy it next", "Top rated by families like yours"],
    "personal": ["Shoppers like you add this", "Runs low about now for most households"],
    "pantry": ["Often bought with your regulars", "New flavor of something you buy"],
    "household": ["Often bought with your regulars", "Most households your size stock this"],
}


def clamp(x: float, lo=0.01, hi=0.99) -> float:
    return round(max(lo, min(hi, x)), 3)


def deep_update(base: dict, upd: dict):
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v


class Gen:
    def __init__(self, export_date: dt.date, seed: int):
        self.d0 = export_date
        self.r = random.Random(seed)
        self.shopper_seq = 200000
        self.member_seq = 500000
        self.order_seq = 0
        self.store_ids = list(C.STORES)

    def next_ids(self):
        self.shopper_seq += 1
        self.member_seq += self.r.randint(3, 97)
        return f"S{self.shopper_seq:08d}", f"{self.member_seq}"

    def order(self, when: dt.date, tier: str, usage: dict, months: float, extra: list | None = None, option=None) -> dict:
        r = self.r
        self.order_seq += 1
        lines = []
        for sku, monthly in usage.items():
            if r.random() < 0.6:
                qty = max(1, min(6, int(round(monthly * months * r.uniform(0.7, 1.4)))))
                unit = C.member_price(sku, tier, qty)
                lines.append({"sku": sku, "name": C.PRODUCT_BY_SKU[sku]["name"], "qty": qty, "unit_price": unit,
                              "line_total": round(unit * qty, 2)})
        for sku in extra or []:
            unit = C.member_price(sku, tier, 1)
            lines.append({"sku": sku, "name": C.PRODUCT_BY_SKU[sku]["name"], "qty": 1, "unit_price": unit, "line_total": unit})
        if not lines:
            sku = next(iter(usage))
            unit = C.member_price(sku, tier, 1)
            lines = [{"sku": sku, "name": C.PRODUCT_BY_SKU[sku]["name"], "qty": 1, "unit_price": unit, "line_total": unit}]
        subtotal = round(sum(l["line_total"] for l in lines), 2)
        return {"order_id": f"SS-{when:%y%m%d}-{10000 + self.order_seq % 90000}", "date": when.isoformat(), "status": "delivered",
                "channel": r.choices(["app", "web", "store"], weights=[62, 23, 15])[0],
                "delivery_option": option or r.choice(C.DELIVERY_OPTIONS), "line_count": len(lines), "lines": lines,
                "subtotal_usd": subtotal, "total_usd": round(subtotal * (1 + C.TAX_RATE), 2), "backordered": []}

    def shopper(self, htype: str | None = None, store_id: str | None = None, overrides: dict | None = None,
                first: str | None = None, last: str | None = None) -> dict:
        r = self.r
        sid, member = self.next_ids()
        htype = htype or r.choice(list(C.HOUSEHOLD_TYPES))
        ht = C.HOUSEHOLD_TYPES[htype]
        store = C.STORES[store_id or r.choice(self.store_ids)]
        first, last = first or r.choice(FIRST), last or r.choice(LAST)
        usage = {sku: round(m * r.uniform(0.6, 1.5), 1) for sku, m in ht["usage"].items() if r.random() < 0.85}
        if not usage:
            usage = dict(ht["usage"])
        tier = r.choices(["MEMBER", "PLUS", "PREMIER"], weights=[55, 32, 13])[0]
        tier_def = C.TIER_BY_CODE[tier]
        cadence = r.choice([7, 10, 14, 14, 21, 28])
        months_per_order = cadence / 30.0
        durable_pool = [p["sku"] for p in C.PRODUCTS if not p["consumable"] and not p.get("limit_per_household")]
        history = []
        when = self.d0 - dt.timedelta(days=r.randint(2, cadence))
        for _ in range(r.randint(4, 8)):
            extra = [r.choice(durable_pool)] if r.random() < 0.18 else []
            history.append(self.order(when, tier, usage, months_per_order, extra))
            when -= dt.timedelta(days=int(cadence * r.uniform(0.7, 1.4)) or 1)
        last_by_sku = {}
        for o in history:
            for l in o["lines"]:
                last_by_sku.setdefault(l["sku"], o["date"])
        replenishment = {sku: {"avg_monthly_qty": m, "last_ordered": last_by_sku.get(sku, history[-1]["date"]),
                               "last_qty": next((l["qty"] for o in history for l in o["lines"] if l["sku"] == sku), None)}
                         for sku, m in usage.items()}
        spend_12 = round(sum(o["total_usd"] for o in history) * r.uniform(2.0, 3.5), 2)
        orders_12 = int(len(history) * r.uniform(2.0, 3.5))
        days_since = {sku: (self.d0 - dt.date.fromisoformat(c["last_ordered"])).days for sku, c in replenishment.items()}
        cart, runout = [], {}
        for sku, c in replenishment.items():
            monthly = c["avg_monthly_qty"]
            last_qty = c["last_qty"] or 1
            days_cover = (last_qty / monthly) * 30 if monthly else 60
            dtr = int(round(days_cover - days_since[sku]))
            runout[sku] = dtr
            if dtr <= 14:
                qty = max(1, min(4, int(round(monthly * months_per_order * 1.1))))
                conf = clamp(0.95 - max(0, dtr) * 0.03 + r.gauss(0, 0.04))
                cart.append({"sku": sku, "qty": qty, "confidence": conf,
                             "reason": "Due for a restock" if dtr > 0 else "Probably out"})
        cart.sort(key=lambda s: -s["confidence"])
        owned = {l["sku"] for o in history for l in o["lines"]}
        rec_pool = [p for p in C.PRODUCTS if p["sku"] not in usage and p["sku"] not in owned and not p.get("limit_per_household")]
        recommended = [{"sku": p["sku"], "confidence": clamp(0.45 + r.random() * 0.5), "reason": r.choice(REC_REASONS[p["category"]])}
                       for p in r.sample(rec_pool, k=min(4, len(rec_pool)))]
        recommended.sort(key=lambda x: -x["confidence"])
        price_watch = []
        if recommended and r.random() < 0.12:
            p = C.PRODUCT_BY_SKU[recommended[0]["sku"]]
            if p["list_price"] >= 30:
                price_watch.append({"sku": p["sku"], "competitor": r.choice(C.COMPETITORS),
                                    "competitor_price_usd": round(p["list_price"] * r.uniform(0.8, 0.9), 2),
                                    "seen_on": (self.d0 - dt.timedelta(days=r.randint(0, 2))).isoformat()})
        last_open = self.d0 - dt.timedelta(days=r.choice([0, 0, 0, 1, 1, 2, 3, 5, 8, 13, 21, 30]))
        churn = clamp(0.1 + (0.3 if (self.d0 - last_open).days > 14 else 0) + r.gauss(0, 0.08))
        affinity = {C.CATEGORIES[cat]["affinity"]: clamp(0.15 + r.random() * 0.6) for cat in C.CATEGORIES}
        for sku in usage:
            affinity[C.CATEGORIES[C.PRODUCT_BY_SKU[sku]["category"]]["affinity"]] = clamp(0.55 + r.random() * 0.4)
        predicted = round(sum(C.member_price(s["sku"], tier, s["qty"]) * s["qty"] for s in cart) * r.uniform(0.9, 1.3), 2)
        size = {"young_family": r.choice([3, 4]), "large_family": r.choice([5, 6]), "busy_professional": 1, "student": 1,
                "couple": 2, "empty_nesters": 2}.get(htype, r.choice([1, 2, 3, 4]))
        favs = sorted({C.PRODUCT_BY_SKU[s]["category"] for s in list(usage)[:3]})[:2] if r.random() < 0.5 else []
        row = {
            "export_date": self.d0.isoformat(),
            "schema_version": SCHEMA_VERSION,
            "shopper_id": sid,
            "member_number": member,
            "first_name": first, "last_name": last,
            "email": f"{first.lower()}.{last.lower().replace(chr(39), '')}@example.com",
            "address": {"street": f"{r.randint(10, 1899)} {r.choice(STREETS)}", "city": store["city"], "state": store["state"],
                        "zip": store["zip"]},
            "region_id": store["region_id"], "region_name": store["region_name"],
            "home_store": {"store_id": store["store_id"], "name": store["name"], "city": store["city"]},
            "household": {"type": htype, "type_name": ht["name"], "size": size, "pets": r.choice(PETS.get(htype, [[]]))},
            "membership": {"tier": tier, "tier_name": tier_def["name"], "discount_pct": tier_def["discount_pct"],
                           "member_since": str(self.d0.year - r.randint(0, 9)),
                           "renewal_date": None if tier == "MEMBER" else (self.d0 + dt.timedelta(days=r.randint(15, 360))).isoformat()},
            "wallet": {"store_credit_usd": r.choice([0, 0, 0, 0, 5.0, 10.0, 12.5, 25.0]), "points": r.randint(0, 6000),
                       "payment_method": r.choice(PAYMENTS).format(r.randint(1000, 9999))},
            "delivery": {"default_option": r.choice(C.DELIVERY_OPTIONS[:3]), "notes": r.choice(DELIVERY_NOTES),
                         "fulfillment_center": store["fulfillment_center"]},
            "preferences": {"favorite_categories": favs, "avoid_categories": [], "allow_substitutions": r.random() < 0.7},
            "engagement": {"last_app_open": last_open.isoformat(), "sessions_30d": r.randint(1, 40),
                           "connectivity_note": r.choice(C.CONNECTIVITY_NOTES), "push_opt_in": r.random() < 0.6},
            "order_history": history,
            "replenishment": replenishment,
            "spend": {"last_12mo_usd": spend_12, "ytd_usd": round(spend_12 * 0.87, 2),
                      "avg_order_usd": round(spend_12 / max(1, orders_12), 2), "orders_last_12mo": orders_12,
                      "category_mix": self._mix(history)},
            "subscriptions": [],
            "ml_scores": {
                "model_version": MODEL_VERSION, "scored_at": f"{self.d0.isoformat()}T04:48:11Z",
                **affinity,
                "churn_risk": churn,
                "predicted_basket_usd": predicted,
                "days_to_runout": runout,
                "replenish_lines": cart[:8],
                "recommended": recommended,
                "price_watch": price_watch,
                "offers": self.offers(sid, tier, churn, affinity, recommended),
                "segment": r.choice(SEGMENTS),
                "next_best_offer": "win_back" if churn > 0.45 else ("replenish" if cart else ("upgrade_to_plus" if tier == "MEMBER" else "discover")),
            },
            "_ingest": {"source_table": SOURCE_TABLE, "pipeline": "shopper_personalization_daily_export", "dbr_version": "16.4 LTS"},
        }
        if overrides:
            deep_update(row, overrides)
        return row

    def offers(self, sid, tier, churn, affinity, recommended):
        """Tailored promotions from loyalty data: what the offer is depends on
        the tier, how likely the shopper is to lapse, and what they buy."""
        r = self.r
        out = []
        top = max(C.CATEGORIES, key=lambda c: affinity[C.CATEGORIES[c]["affinity"]])
        exp = (self.d0 + dt.timedelta(days=r.randint(3, 10))).isoformat()
        if churn > 0.45:
            out.append({"offer_id": f"OF-{sid[-6:]}-WB", "kind": "category_pct", "category": top, "pct_off": 15,
                        "title": f"Welcome back: 15% off {C.CATEGORIES[top]['name']}", "driver": "churn risk",
                        "reason": "You haven't ordered in a while, and this is the aisle you shop most", "expires": exp, "once": True})
        elif tier != "MEMBER":
            out.append({"offer_id": f"OF-{sid[-6:]}-CAT", "kind": "category_pct", "category": top, "pct_off": 10,
                        "title": f"{C.TIER_BY_CODE[tier]['name']} offer: 10% off {C.CATEGORIES[top]['name']}", "driver": "loyalty tier",
                        "reason": "Your most-shopped category this quarter", "expires": exp, "once": False})
        else:
            out.append({"offer_id": f"OF-{sid[-6:]}-PTS", "kind": "bonus_points", "min_subtotal_usd": 75, "points": 500,
                        "title": "500 bonus points on $75 or more", "driver": "loyalty tier",
                        "reason": "You're 1 order away from your next reward", "expires": exp, "once": True})
        if recommended and r.random() < 0.4:
            p = C.PRODUCT_BY_SKU[recommended[0]["sku"]]
            out.append({"offer_id": f"OF-{sid[-6:]}-SKU", "kind": "sku_pct", "sku": p["sku"], "pct_off": 10,
                        "title": f"10% off {p['name'].split(',')[0]}", "driver": "browsing history",
                        "reason": "You looked at it this week", "expires": exp, "once": True})
        return out

    def _mix(self, history):
        tot = {}
        for o in history:
            for l in o["lines"]:
                cat = C.PRODUCT_BY_SKU[l["sku"]]["category"]
                tot[cat] = tot.get(cat, 0) + l["line_total"]
        s = sum(tot.values()) or 1
        return {k: round(v / s, 3) for k, v in sorted(tot.items(), key=lambda kv: -kv[1])}

    def rows(self, n: int):
        for _ in range(n):
            yield self.shopper()

    # -- personas -------------------------------------------------------------------
    def _line(self, sku, qty, tier):
        unit = C.member_price(sku, tier, qty)
        return {"sku": sku, "name": C.PRODUCT_BY_SKU[sku]["name"], "qty": qty, "unit_price": unit, "line_total": round(unit * qty, 2)}

    def _past_order(self, oid_seq, days_ago, tier, lines, option):
        when = self.d0 - dt.timedelta(days=days_ago)
        ls = [self._line(s, q, tier) for s, q in lines]
        sub = round(sum(l["line_total"] for l in ls), 2)
        return {"order_id": f"SS-{when:%y%m%d}-{oid_seq}", "date": when.isoformat(), "status": "delivered", "channel": "app",
                "delivery_option": option, "line_count": len(ls), "lines": ls, "subtotal_usd": sub,
                "total_usd": round(sub * (1 + C.TAX_RATE), 2), "backordered": []}

    def _replenishment(self, items):
        """items: sku -> (avg monthly qty, days since last order, last qty)"""
        return {sku: {"avg_monthly_qty": m, "last_ordered": (self.d0 - dt.timedelta(days=ago)).isoformat(), "last_qty": q}
                for sku, (m, ago, q) in items.items()}

    def personas(self):
        """Hand-built demo shoppers. Everything is relative to the export
        date, so the story is the same in every daily export."""
        d0 = self.d0
        rows = []

        # 1. Maya Chen - Salem, MA. Plus member, young family with a dog, subway commute.
        rows.append(self.shopper("young_family", "ST-0101", first="Maya", last="Chen", overrides={
            "shopper_id": "S00101001", "member_number": "204817", "email": "maya.chen@example.com",
            "address": {"street": "212 Derby St", "city": "Salem", "state": "MA", "zip": "01970"},
            "household": {"type": "young_family", "type_name": "Young family", "size": 3, "pets": ["1 dog"]},
            "membership": {"tier": "PLUS", "tier_name": "Plus", "discount_pct": 5, "member_since": "2021",
                           "renewal_date": (d0 + dt.timedelta(days=38)).isoformat()},
            "wallet": {"store_credit_usd": 12.5, "points": 1840, "payment_method": "Visa ending in 4417"},
            "delivery": {"default_option": "Same-day delivery", "notes": "Leave at the side door", "fulfillment_center": "FC-NE"},
            "preferences": {"favorite_categories": ["baby", "kitchen"], "avoid_categories": [], "allow_substitutions": True},
            "engagement": {"last_app_open": d0.isoformat(), "sessions_30d": 31,
                           "connectivity_note": "Subway commute - no signal in the tunnels", "push_opt_in": True},
            "order_history": [
                self._past_order(48213, 9, "PLUS", [("SS-440129", 1), ("SS-440263", 1), ("SS-220134", 1), ("SS-111046", 2)], "Same-day delivery"),
                self._past_order(47102, 21, "PLUS", [("SS-110245", 2), ("SS-550118", 1), ("SS-220391", 1), ("SS-220788", 1)], "Same-day delivery"),
                self._past_order(45530, 36, "PLUS", [("SS-440129", 1), ("SS-110245", 1), ("SS-660573", 1), ("SS-220268", 1)], "Store pickup"),
                self._past_order(44018, 52, "PLUS", [("SS-550118", 1), ("SS-440263", 1), ("SS-220134", 1), ("SS-330117", 1)], "2-day shipping"),
            ],
            "replenishment": self._replenishment({
                "SS-110245": (2.5, 21, 2), "SS-440129": (1.2, 9, 1), "SS-220134": (1.0, 9, 1), "SS-550118": (1.0, 21, 1),
                "SS-440263": (1.0, 9, 1), "SS-220391": (0.8, 21, 1), "SS-111046": (2.0, 9, 2), "SS-220788": (0.8, 21, 1)}),
            "spend": {"last_12mo_usd": 3184.40, "ytd_usd": 2790.15, "avg_order_usd": 122.48, "orders_last_12mo": 26,
                      "category_mix": {"baby": 0.34, "household": 0.24, "pantry": 0.2, "pets": 0.14, "kitchen": 0.08}},
            "ml_scores": {"segment": "New Parents", "next_best_offer": "replenish", "churn_risk": 0.06,
                          "baby_affinity": 0.95, "kitchen_affinity": 0.82, "pantry_affinity": 0.88, "household_affinity": 0.86,
                          "pets_affinity": 0.74,
                          "replenish_lines": [
                              {"sku": "SS-110245", "qty": 2, "confidence": 0.96, "reason": "Probably out"},
                              {"sku": "SS-440129", "qty": 1, "confidence": 0.94, "reason": "Due for a restock"},
                              {"sku": "SS-220134", "qty": 1, "confidence": 0.9, "reason": "Due for a restock"},
                              {"sku": "SS-550118", "qty": 1, "confidence": 0.88, "reason": "Due for a restock"},
                              {"sku": "SS-440263", "qty": 1, "confidence": 0.86, "reason": "Due for a restock"},
                              {"sku": "SS-220391", "qty": 1, "confidence": 0.81, "reason": "Due for a restock"},
                              {"sku": "SS-111046", "qty": 2, "confidence": 0.74, "reason": "Due for a restock"},
                              {"sku": "SS-220788", "qty": 1, "confidence": 0.58, "reason": "Due for a restock"}],
                          "days_to_runout": {"SS-110245": 1, "SS-440129": 3, "SS-220134": 5, "SS-550118": 6,
                                             "SS-440263": 8, "SS-220391": 10, "SS-111046": 12, "SS-220788": 14},
                          "recommended": [
                              {"sku": "SS-660102", "confidence": 0.93, "reason": "Viewed 4 times this month"},
                              {"sku": "SS-660341", "confidence": 0.84, "reason": "Brews the pods you already buy"},
                              {"sku": "SS-880365", "confidence": 0.71, "reason": "Top pick for families like yours"},
                              {"sku": "SS-770114", "confidence": 0.62, "reason": "Popular with commuters like you"}],
                          "price_watch": [{"sku": "SS-660102", "competitor": "Brightway Market", "competitor_price_usd": 239.99,
                                           "seen_on": d0.isoformat()}],
                          "offers": [
                              {"offer_id": "OF-MAYA-BABY", "kind": "category_pct", "category": "baby", "pct_off": 10,
                               "title": "10% off Baby this week", "driver": "purchase history",
                               "reason": "You've bought diapers 6 months running", "expires": (d0 + dt.timedelta(days=6)).isoformat(), "once": False},
                              {"offer_id": "OF-MAYA-PTS", "kind": "bonus_points", "min_subtotal_usd": 150, "points": 500,
                               "title": "500 bonus points on $150 or more", "driver": "loyalty tier",
                               "reason": "Plus members who order weekly get a points boost", "expires": (d0 + dt.timedelta(days=3)).isoformat(), "once": True}],
                          "predicted_basket_usd": 262.0},
        }))

        # 2. Devon Walker - Rhinebeck, NY. Member tier, two dogs, rural dead zones.
        rows.append(self.shopper("pet_household", "ST-0202", first="Devon", last="Walker", overrides={
            "shopper_id": "S00101002", "member_number": "318552", "email": "devon.walker@example.com",
            "address": {"street": "6400 Montgomery St", "city": "Rhinebeck", "state": "NY", "zip": "12572"},
            "household": {"type": "pet_household", "type_name": "Pet household", "size": 2, "pets": ["2 dogs"]},
            "membership": {"tier": "MEMBER", "tier_name": "Member", "discount_pct": 0, "member_since": "2023", "renewal_date": None},
            "wallet": {"store_credit_usd": 0, "points": 412, "payment_method": "Mastercard ending in 8830"},
            "delivery": {"default_option": "2-day shipping", "notes": "Back porch, not the front steps", "fulfillment_center": "FC-NY"},
            "preferences": {"favorite_categories": ["pets", "outdoor"], "avoid_categories": ["baby"], "allow_substitutions": False},
            "engagement": {"last_app_open": d0.isoformat(), "sessions_30d": 12,
                           "connectivity_note": "Rural drive home - dead zone on the county road", "push_opt_in": False},
            "order_history": [
                self._past_order(51877, 12, "MEMBER", [("SS-550472", 1), ("SS-220642", 1), ("SS-110245", 1)], "2-day shipping"),
                self._past_order(50341, 27, "MEMBER", [("SS-550118", 2), ("SS-550472", 1), ("SS-220134", 1)], "Store pickup"),
                self._past_order(49006, 55, "MEMBER", [("SS-550118", 2), ("SS-990107", 1), ("SS-220788", 1)], "2-day shipping"),
            ],
            "replenishment": self._replenishment({
                "SS-550118": (2.0, 27, 2), "SS-550472": (1.5, 12, 1), "SS-220642": (0.5, 12, 1), "SS-110245": (1.0, 12, 1),
                "SS-220134": (1.0, 27, 1)}),
            "spend": {"last_12mo_usd": 1412.90, "ytd_usd": 1230.02, "avg_order_usd": 88.31, "orders_last_12mo": 16,
                      "category_mix": {"pets": 0.61, "household": 0.2, "pantry": 0.12, "outdoor": 0.07}},
            "ml_scores": {"segment": "Pet Parents", "next_best_offer": "upgrade_to_plus", "churn_risk": 0.17,
                          "pets_affinity": 0.97, "outdoor_affinity": 0.8, "household_affinity": 0.7, "pantry_affinity": 0.6,
                          "baby_affinity": 0.03,
                          "replenish_lines": [
                              {"sku": "SS-550118", "qty": 2, "confidence": 0.97, "reason": "Probably out"},
                              {"sku": "SS-550472", "qty": 1, "confidence": 0.89, "reason": "Due for a restock"},
                              {"sku": "SS-220134", "qty": 1, "confidence": 0.83, "reason": "Due for a restock"},
                              {"sku": "SS-110245", "qty": 1, "confidence": 0.7, "reason": "Due for a restock"}],
                          "days_to_runout": {"SS-550118": 0, "SS-550472": 6, "SS-220134": 3, "SS-110245": 13, "SS-220642": 40},
                          "recommended": [
                              {"sku": "SS-550590", "confidence": 0.9, "reason": "Pet parents with your basket add this"},
                              {"sku": "SS-990451", "confidence": 0.77, "reason": "Matches your weekend buys"},
                              {"sku": "SS-440485", "confidence": 0.3, "reason": "Popular in your area"}],
                          "price_watch": [], "predicted_basket_usd": 146.0,
                          "offers": [
                              {"offer_id": "OF-DEVON-BED", "kind": "sku_pct", "sku": "SS-550590", "pct_off": 15,
                               "title": "15% off the Orthopedic Pet Bed", "driver": "browsing history",
                               "reason": "You looked at it twice, and it's the top add-on for two-dog households",
                               "expires": (d0 + dt.timedelta(days=5)).isoformat(), "once": True},
                              {"offer_id": "OF-DEVON-OUT", "kind": "category_pct", "category": "outdoor", "pct_off": 10,
                               "title": "10% off Sports & outdoors", "driver": "purchase history",
                               "reason": "Your weekend buys lean outdoors", "expires": (d0 + dt.timedelta(days=9)).isoformat(), "once": False}]},
        }))

        # 3. Sofia Alvarez - Plano, TX. Premier member, household of five, bought the limited console last week.
        rows.append(self.shopper("large_family", "ST-0304", first="Sofia", last="Alvarez", overrides={
            "shopper_id": "S00101003", "member_number": "427190", "email": "sofia.alvarez@example.com",
            "address": {"street": "3100 Independence Pkwy", "city": "Plano", "state": "TX", "zip": "75074"},
            "household": {"type": "large_family", "type_name": "Large family", "size": 5, "pets": []},
            "membership": {"tier": "PREMIER", "tier_name": "Premier", "discount_pct": 10, "member_since": "2018",
                           "renewal_date": (d0 + dt.timedelta(days=204)).isoformat()},
            "wallet": {"store_credit_usd": 0, "points": 9620, "payment_method": "ShopSmart Card ending in 2291"},
            "delivery": {"default_option": "Same-day delivery", "notes": "Ring the bell - dog barks, that's fine", "fulfillment_center": "FC-SC"},
            "preferences": {"favorite_categories": ["electronics", "toys"], "avoid_categories": [], "allow_substitutions": True},
            "engagement": {"last_app_open": d0.isoformat(), "sessions_30d": 44,
                           "connectivity_note": "Shops in-store, deep in the aisles - weak signal", "push_opt_in": True},
            "order_history": [
                self._past_order(60412, 6, "PREMIER", [("SS-770229", 1), ("SS-880243", 1), ("SS-110903", 2), ("SS-220268", 2)], "Same-day delivery"),
                self._past_order(59120, 15, "PREMIER", [("SS-220134", 2), ("SS-220391", 2), ("SS-110630", 2), ("SS-111046", 2), ("SS-220579", 1)], "Same-day delivery"),
                self._past_order(57733, 29, "PREMIER", [("SS-220268", 2), ("SS-110777", 3), ("SS-330117", 1), ("SS-770552", 1)], "Store pickup"),
                self._past_order(56290, 44, "PREMIER", [("SS-220134", 2), ("SS-220642", 1), ("SS-110388", 1), ("SS-330396", 1)], "Same-day delivery"),
            ],
            "replenishment": self._replenishment({
                "SS-220134": (2.0, 15, 2), "SS-220391": (1.5, 15, 2), "SS-110630": (2.0, 15, 2), "SS-111046": (2.0, 15, 2),
                "SS-220268": (2.0, 6, 2), "SS-110777": (2.0, 29, 3), "SS-220579": (1.0, 15, 1), "SS-110903": (2.0, 6, 2)}),
            "spend": {"last_12mo_usd": 7921.75, "ytd_usd": 6990.40, "avg_order_usd": 164.20, "orders_last_12mo": 48,
                      "category_mix": {"household": 0.33, "pantry": 0.27, "electronics": 0.22, "toys": 0.1, "personal": 0.08}},
            "ml_scores": {"segment": "Premium Explorers", "next_best_offer": "discover", "churn_risk": 0.04,
                          "electronics_affinity": 0.93, "toys_affinity": 0.9, "household_affinity": 0.9, "pantry_affinity": 0.86,
                          "replenish_lines": [
                              {"sku": "SS-110777", "qty": 3, "confidence": 0.95, "reason": "Probably out"},
                              {"sku": "SS-220134", "qty": 2, "confidence": 0.91, "reason": "Due for a restock"},
                              {"sku": "SS-110630", "qty": 2, "confidence": 0.87, "reason": "Due for a restock"},
                              {"sku": "SS-111046", "qty": 2, "confidence": 0.84, "reason": "Due for a restock"},
                              {"sku": "SS-220391", "qty": 2, "confidence": 0.78, "reason": "Due for a restock"},
                              {"sku": "SS-220579", "qty": 1, "confidence": 0.69, "reason": "Due for a restock"}],
                          "days_to_runout": {"SS-110777": 0, "SS-220134": 4, "SS-110630": 6, "SS-111046": 7, "SS-220391": 9,
                                             "SS-220579": 13, "SS-220268": 24, "SS-110903": 24},
                          "recommended": [
                              {"sku": "SS-770336", "confidence": 0.9, "reason": "Goes with your recent order"},
                              {"sku": "SS-880121", "confidence": 0.86, "reason": "Top pick for families like yours"},
                              {"sku": "SS-770447", "confidence": 0.72, "reason": "Popular with shoppers like you"},
                              {"sku": "SS-660237", "confidence": 0.66, "reason": "Shoppers with your basket upgrade to this"}],
                          "price_watch": [{"sku": "SS-770336", "competitor": "Oakline Stores", "competitor_price_usd": 184.99,
                                           "seen_on": (d0 - dt.timedelta(days=1)).isoformat()}],
                          "offers": [
                              {"offer_id": "OF-SOFIA-TOYS", "kind": "category_pct", "category": "toys", "pct_off": 10,
                               "title": "Premier early access: 10% off Toys & games", "driver": "loyalty tier",
                               "reason": "Premier members see the holiday toy offer a week early",
                               "expires": (d0 + dt.timedelta(days=7)).isoformat(), "once": False},
                              {"offer_id": "OF-SOFIA-PTS", "kind": "bonus_points", "min_subtotal_usd": 200, "points": 1000,
                               "title": "1,000 bonus points on $200 or more", "driver": "loyalty tier",
                               "reason": "A thank-you for 48 orders this year", "expires": (d0 + dt.timedelta(days=4)).isoformat(), "once": True}],
                          "predicted_basket_usd": 171.0},
        }))
        return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export-date", default=dt.date.today().isoformat())
    ap.add_argument("--target-mb", type=float, default=100.0, help="approximate total size in MiB")
    ap.add_argument("--parts", type=int, default=4, help="number of part files (keep each under GitHub's 50MB warning)")
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "databricks-dump"))
    args = ap.parse_args(argv)

    export_date = dt.date.fromisoformat(args.export_date)
    g = Gen(export_date, args.seed)
    target = int(args.target_mb * 1024 * 1024)
    per_part = target // args.parts + 1
    out_dir = os.path.abspath(os.path.join(args.out, f"dt={export_date.isoformat()}"))
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("part-") or f in ("_SUCCESS", "_manifest.json", "_pricing_signals.json"):
            os.remove(os.path.join(out_dir, f))

    job_uuid = uuid.UUID(int=g.r.getrandbits(128))
    written, rows = 0, 0
    files = []
    pending = g.personas()
    for part in range(args.parts):
        name = f"part-{part:05d}-{job_uuid}-c000.json"
        path = os.path.join(out_dir, name)
        part_bytes, part_rows = 0, 0
        with open(path, "w", encoding="utf-8") as fh:
            while part_bytes < per_part and written < target:
                if not pending:
                    pending = list(g.rows(500))
                line = json.dumps(pending.pop(0), separators=(",", ":"), ensure_ascii=False) + "\n"
                b = len(line.encode("utf-8"))
                fh.write(line)
                part_bytes += b
                written += b
                part_rows += 1
        rows += part_rows
        files.append({"file": name, "rows": part_rows, "bytes": part_bytes})
        print(f"  {name}  {part_rows:>7,} rows  {part_bytes / 1048576:6.1f} MiB", flush=True)

    manifest = {
        "table": SOURCE_TABLE,
        "export_date": export_date.isoformat(), "schema_version": SCHEMA_VERSION, "model_version": MODEL_VERSION,
        "format": "jsonl", "rows": rows, "stores": len(C.STORES), "bytes": written, "files": files,
        "generator": "databricks/generate_mock_dump.py", "seed": args.seed,
        "demo_shoppers": ["S00101001", "S00101002", "S00101003"], "pricing_signals": "_pricing_signals.json",
    }
    # What the pricing engine reacts to besides inventory. In production these are the outputs of the
    # competitor price pipeline, the demand forecast and market-basket analysis.
    signals = {
        "source": f"databricks export {export_date.isoformat()}", "export_date": export_date.isoformat(),
        "market_prices": {sku: {"competitor": c, "price_usd": p} for sku, (c, p) in C.MARKET_PRICES.items()},
        "high_demand": list(C.HIGH_DEMAND),
        "time_windows": C.TIME_WINDOWS,
        "bundles": C.BUNDLES,
        "pairs_with": {k: [{"sku": x, "reason": why} for x, why in v] for k, v in C.PAIRS_WITH.items()},
        "upgrades": {k: {"sku": x, "reason": why} for k, (x, why) in C.UPGRADES.items()},
    }
    with open(os.path.join(out_dir, "_pricing_signals.json"), "w") as fh:
        json.dump(signals, fh, indent=2)
    with open(os.path.join(out_dir, "_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    open(os.path.join(out_dir, "_SUCCESS"), "w").close()
    print(f"Wrote {rows:,} shoppers ({len(C.STORES)} home stores) = {written / 1048576:.1f} MiB to {out_dir}")


if __name__ == "__main__":
    main()
