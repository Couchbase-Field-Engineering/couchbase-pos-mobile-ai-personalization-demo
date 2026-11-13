"""Targeted recommendations and dynamic pricing & bundling, in memory mode.

Runs after test_demo_flows.py against the same backend, so it uses shoppers
that file leaves alone wherever a clean state matters.
"""
import time
import uuid

import pytest

EXPORT = "2026-11-13"

from app import catalog as C  # noqa: E402

CATEGORY = {p["sku"]: p["category"] for p in C.PRODUCTS}


def login(client, row):
    r = client.post("/api/auth/login", json={"member_number": row["member_number"], "last_name": row["last_name"]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["shopper_id"]


def pull(client, h):
    return {(d["collection"], d["id"]): d["doc"] for d in client.get("/api/sync/pull", headers=h).json()["docs"]}


def push(client, h, sid, tool, args):
    oid = f"order::{sid}::{uuid.uuid4().hex[:12]}"
    r = client.post("/api/sync/push", headers=h, json={"docs": [{"collection": "orders", "id": oid, "doc": {
        "type": "request", "shopper_id": sid, "tool": tool, "args": args, "created_at": f"{EXPORT}T01:00:00Z"}}]})
    assert r.status_code == 200, r.text
    for _ in range(50):
        d = pull(client, h).get(("orders", oid))
        if d and d["status"] not in ("pending", "processing"):
            return d
        time.sleep(0.1)
    raise AssertionError("order never processed")


def sim(client, **body):
    r = client.post("/api/merch/simulate", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def fresh(rows, fc, tier=None, untouched=("pantry", "household", "personal", "outdoor")):
    """A shopper from the export that no other test signs in as, with no
    personal offer on the categories the test prices."""
    def clean(r):
        for o in r["ml_scores"]["offers"]:
            cat = o.get("category") or (o.get("sku") and CATEGORY[o["sku"]])
            if cat in untouched:
                return False
        return True
    return next(r for r in rows[3:] if r["delivery"]["fulfillment_center"] in (fc if isinstance(fc, tuple) else (fc,))
                and (tier is None or r["membership"]["tier"] == tier) and clean(r))


def product(state, sku):
    return next(p for p in state["products"] if p["sku"] == sku)


def adj(line):
    return {a["rule"]: a for a in line["adjustments"]}


def test_rules_reach_the_phone_and_inventory_shapes_bundles(client, rows):
    h, sid = login(client, rows[0])  # Maya, Northeast
    docs = pull(client, h)
    pricing = docs[("catalog", "pricing::FC-NE")]
    assert pricing["version"] and pricing["clock"]["demo_hour"] == 9
    bundles = {b["id"]: b for b in pricing["bundles"]}
    # Coffee pods are nearly gone in the Northeast: no promotions on them, and the bundle that needs them is paused.
    assert bundles["coffee-starter"]["active"] is False and "sold out" in bundles["coffee-starter"]["paused_reason"]
    assert bundles["baby-essentials"]["active"]
    # The engine built a bundle of its own around the overstocked paper towels.
    assert bundles["clear-out-SS-220134"]["kind"] == "dynamic" and bundles["clear-out-SS-220134"]["skus"] == ["SS-220642", "SS-220134"]
    cat = docs[("catalog", "catalog::v1")]
    assert cat["pairs_with"]["SS-110245"][0]["sku"] == "SS-660341" and cat["upgrades"]["SS-110245"]["sku"] == "SS-110260"
    # only her own fulfillment center's rules replicate
    assert [k for k in docs if k[1].startswith("pricing::")] == [("catalog", "pricing::FC-NE")]


def test_overstock_markdown_follows_inventory(client, rows):
    row = fresh(rows, "FC-MW")
    h, sid = login(client, row)
    tier_pct = row["membership"]["discount_pct"]
    soap = "SS-220455"
    st = sim(client, fc="FC-MW", action="set_inventory", sku=soap, value=300)
    assert product(st, soap)["rules"]["overstock"]["pct_off"] == 15
    assert any("Lemon Grove Dish Soap" in c["text"] and "15% off" in c["text"] for c in st["changes"])
    assert pull(client, h)[("catalog", "pricing::FC-MW")]["sku_rules"][soap]["overstock"]["pct_off"] == 15
    r = push(client, h, sid, "place_order", {"lines": [{"sku": soap, "qty": 2}]})
    line = r["item"]["lines"][0]
    assert adj(line)["overstock"]["pct"] == 15 and line["unit_price"] == round(10.49 * (1 - (tier_pct + 15) / 100), 2)
    assert {"label": "Overstock markdown", "saved_usd": round(10.49 * 0.15 * 2, 2)} in r["item"]["promotions"]
    # Nearly sold out: the markdown ends and every promotion on the item is paused.
    st = sim(client, fc="FC-MW", action="set_inventory", sku=soap, value=4)
    assert "overstock" not in product(st, soap)["rules"] and product(st, soap)["rules"]["no_promo"]["label"] == "Selling fast"
    r = push(client, h, sid, "place_order", {"lines": [{"sku": soap, "qty": 1}]})
    assert "overstock" not in adj(r["item"]["lines"][0]) and r["item"]["lines"][0]["unit_price"] == round(10.49 * (1 - tier_pct / 100), 2)


def test_hour_promotions_and_price_lock(client, rows):
    row = fresh(rows, "FC-W")
    h, sid = login(client, row)
    morning = pull(client, h)[("catalog", "pricing::FC-W")]["version"]
    oats = [{"sku": "SS-110388", "qty": 1}]  # tagged breakfast: 10% off from 6 to 10 am
    try:
        r = push(client, h, sid, "place_order", {"lines": oats})
        assert adj(r["item"]["lines"][0])["time"]["label"] == "Morning coffee run"
        st = sim(client, fc="FC-W", action="set_hour", value=11)
        assert st["hour"] == 11 and not any(w["active"] for w in st["time_rules"])
        # An order placed now pays the 11 am price...
        r = push(client, h, sid, "place_order", {"lines": oats})
        assert "time" not in adj(r["item"]["lines"][0]) and r["item"]["price_locked"] is False
        # ...but a cart priced on the phone at 9 am, syncing late, keeps the price the shopper saw.
        r = push(client, h, sid, "place_order", {"lines": oats, "pricing_version": morning, "priced_hour": 9})
        assert adj(r["item"]["lines"][0])["time"]["pct"] == 10 and r["item"]["price_locked"] is True
        # Evening peak: pickup earns the discount, delivery doesn't.
        sim(client, fc="FC-W", action="set_hour", value=18)
        r = push(client, h, sid, "place_order", {"lines": oats, "delivery_option": "Store pickup"})
        assert adj(r["item"]["lines"][0])["time"]["label"] == "Skip the rush"
        r = push(client, h, sid, "place_order", {"lines": oats, "delivery_option": "2-day shipping"})
        assert "time" not in adj(r["item"]["lines"][0])
    finally:
        sim(client, fc="FC-W", action="set_hour", value=9)


def test_competitor_prices_auto_match_within_guardrail(client, rows):
    bottle = "SS-990107"  # list $27.99
    try:
        st = sim(client, fc="FC-SE", action="set_competitor_price", sku=bottle, value=26.5, competitor="Oakline Stores")
        assert product(st, bottle)["rules"]["competitor"]["price_cap"] == 26.5 and product(st, bottle)["shelf_price"] == 26.5
        row = fresh(rows, "FC-SE", "MEMBER")
        h, sid = login(client, row)
        r = push(client, h, sid, "place_order", {"lines": [{"sku": bottle, "qty": 1}]})
        assert r["item"]["lines"][0]["unit_price"] == 26.5 and adj(r["item"]["lines"][0])["competitor"]["label"] == "Matched to Oakline Stores"
        # Too far below list to match automatically: that gap is a price-match request for Customer Care.
        st = sim(client, fc="FC-SE", action="set_competitor_price", sku=bottle, value=20)
        assert "competitor" not in product(st, bottle)["rules"] and product(st, bottle)["shelf_price"] == 27.99
    finally:
        sim(client, fc="FC-SE", action="set_competitor_price", sku=bottle, value=None)


def test_bundle_offer_cross_sell_and_bonus_points(client, rows):
    before = client.get("/api/status").json()["metrics"]["merchandising"]
    # Devon: a one-time 15% offer on the pet bed, suggested at checkout as a cross-sell.
    h, sid = login(client, rows[1])
    assert "OF-DEVON-BED" in [o["offer_id"] for o in pull(client, h)[("recommendations", f"recs::{sid}")]["offers"]]
    r = push(client, h, sid, "place_order", {"lines": [{"sku": "SS-550590", "qty": 1}, {"sku": "SS-880243", "qty": 1},
                                                      {"sku": "SS-110903", "qty": 1}, {"sku": "SS-110777", "qty": 1}],
                                            "sources": {"SS-550590": "cross_sell", "SS-880243": "bundle"}})
    lines = {l["sku"]: l for l in r["item"]["lines"]}
    assert lines["SS-550590"]["unit_price"] == 50.99 and adj(lines["SS-550590"])["offer"]["id"] == "OF-DEVON-BED"
    assert lines["SS-550590"]["source"] == "cross_sell"
    assert all(adj(lines[s])["bundle"]["id"] == "game-night" for s in ("SS-880243", "SS-110903", "SS-110777"))
    assert r["item"]["bundles_applied"] == ["game-night"] and "OF-DEVON-BED" in r["item"]["offers_applied"]
    assert "Game night bundle" in r["summary"]
    # one-time: it is gone from his phone after the order
    assert "OF-DEVON-BED" not in [o["offer_id"] for o in pull(client, h)[("recommendations", f"recs::{sid}")]["offers"]]
    after = client.get("/api/status").json()["metrics"]["merchandising"]
    assert after["revenue_by_source"]["cross_sell"] >= before["revenue_by_source"].get("cross_sell", 0) + 50.99
    assert after["bundles_sold"] == before["bundles_sold"] + 1 and after["offers_redeemed"] > before["offers_redeemed"]
    # Sofia: 1,000 bonus points for spending $200 or more.
    h, sid = login(client, rows[2])
    r = push(client, h, sid, "place_order", {"lines": [{"sku": "SS-660102", "qty": 1}]})
    assert r["item"]["bonus_points"] == 1000 and r["item"]["points_earned"] == int(r["item"]["subtotal_usd"]) * 3 + 1000


def test_discounts_are_capped(client, rows):
    row = fresh(rows, ("FC-MA", "FC-NY", "FC-SE"), "PREMIER")
    h, sid = login(client, row)
    sunscreen = "SS-330431"  # overstocked everywhere: Premier 10 + multi-buy 10 + overstock 15 + its clear-out bundle 12 = 47
    r = push(client, h, sid, "place_order", {"lines": [{"sku": sunscreen, "qty": 6}, {"sku": "SS-990451", "qty": 1}]})
    line = next(l for l in r["item"]["lines"] if l["sku"] == sunscreen)
    assert sum(a["pct"] for a in line["adjustments"] if "pct" in a) > 40
    assert line["unit_price"] == round(14.99 * 0.6, 2)  # capped at 40% off list


def test_assistant_knows_the_deals(client, rows):
    h, sid = login(client, rows[2])
    a = client.post("/api/assistant", headers=h, json={"message": "Any deals for me today?"}).json()
    assert a["intent"] == "deals" and a["personal"]
    assert "Toys & games" in a["reply"] and "Morning coffee run" in a["reply"] and "Bundle" in a["reply"]
    g = client.post("/api/assistant", headers=h, json={"message": "Why did a price change today?"}).json()
    assert g["personal"] is False and "overstocked" in g["reply"]
    p = client.post("/api/assistant", headers=h, json={"message": "How much are the coffee pods?"}).json()
    assert any(x.get("args", {}).get("sku") == "SS-110260" for x in p["actions"])  # the upsell: the value box


def test_console_state_and_reset(client):
    st = client.get("/api/merch/state?fc=FC-NE").json()
    assert st["hour"] == 9 and len(st["products"]) == 51 and st["engine"]["signals_source"].startswith("databricks export")
    assert product(st, "SS-220134")["shelf_price"] == round(24.99 * 0.85, 2)
    assert client.get("/api/merch/state?fc=NOPE").status_code == 404
    assert client.post("/api/merch/simulate", json={"fc": "FC-NE", "action": "nope"}).status_code == 400
    st = sim(client, fc="FC-MW", action="reset")
    assert "overstock" not in product(st, "SS-220455")["rules"] and "no_promo" not in product(st, "SS-220455")["rules"]
