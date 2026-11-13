"""The store side in memory mode (no Couchbase, no AOM): the voice ordering
agent at the drive-thru lane and on the phone line, and the self-checkout
vision agent.

These tests run against their own backend process, so the demo shoppers are
exactly as the export left them (the other test modules place orders as Maya,
Devon and Sofia). Each test uses its own store, so kitchen counts, tickets and
the WAN lever don't leak between tests. The pricing clock is pinned to 9 am.
"""
import os
import subprocess
import sys
import time

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API = "http://127.0.0.1:18203"
ENV = {"STORE_DRIVER": "memory", "AOM_URL": "", "SG_ADMIN_URL": "", "DEMO_HOUR": "9", "REQUEST_POLL_SECONDS": "0.2"}

MAYA, DEVON, SOFIA = "S00101001", "S00101002", "S00101003"
SALEM, RHINEBECK, PLANO = "ST-0101", "ST-0202", "ST-0304"
PODS_72, PODS_120, EARBUDS, EARBUDS_PRO, CONSOLE, TOWELS, TRASH_BAGS = (
    "SS-110245", "SS-110260", "SS-770114", "SS-770128", "SS-770229", "SS-220134", "SS-220642")


@pytest.fixture(scope="module")
def client(dump):
    """A fresh in-memory backend on its own port."""
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", API.rsplit(":", 1)[1]],
                            cwd=os.path.join(ROOT, "backend"), env={**os.environ, **ENV, "DUMP_DIR": str(dump)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c = httpx.Client(base_url=API, timeout=30)
    try:
        for _ in range(120):
            try:
                if c.get("/api/health").json()["load"] == "loaded":
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("backend never came up")
        yield c
    finally:
        c.close()
        proc.terminate()


@pytest.fixture(scope="module")
def app_modules(dump):
    """The app's modules for the pure-function tests, configured the way conftest configures the in-process backend."""
    os.environ.update(ENV, DUMP_DIR=str(dump))
    from app import menu, vision, voice
    return menu, vision, voice


def say(client, sid, text):
    r = client.post(f"/api/voice/session/{sid}/turn", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def lane(client, store, **who):
    r = client.post("/api/voice/session", json={"store_id": store, **who})
    assert r.status_code == 200, r.text
    return r.json()


def settled(client, url, key="ticket"):
    for _ in range(60):
        d = client.get(url).json()
        if d.get(key) and d[key]["status"] not in ("pending", "processing"):
            return d
        time.sleep(0.1)
    raise AssertionError("ticket never processed")


def kiosk(client, store, **who):
    r = client.post("/api/kiosk/session", json={"store_id": store, **who})
    assert r.status_code == 200, r.text
    return r.json()


def ev(client, sid, kind, **data):
    r = client.post(f"/api/kiosk/session/{sid}/event", json={"kind": kind, "data": data})
    assert r.status_code == 200, r.text
    return r.json()


def lines(view):
    return {l["sku"]: l for l in view["cart"]}


# --------------------------------------------------------------------------
# understanding speech
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("two large iced lattes with oat milk and a breakfast burrito no cheese",
     [("CF-1001", 2, "large", ["iced", "oat_milk"]), ("CF-2002", 1, None, ["no_cheese"])]),
    ("can I get a small decaf coffee, a bacon egg and cheese with avocado, and three hash browns",
     [("CF-1002", 1, "small", ["decaf"]), ("CF-2001", 1, None, ["add_avocado"]), ("CF-2003", 3, None, [])]),
    ("a crispy chicken sandwich, spicy, no pickles, medium fries and a lemonade",
     [("CF-3001", 1, None, ["no_pickles", "spicy"]), ("CF-3003", 1, "medium", []), ("CF-1005", 1, "medium", [])]),
    ("a dozen cookies and my coffee pods", [("CF-4001", 12, None, []), ("SS-110245", 1, None, [])]),
    ("cold brew, large, with vanilla", [("CF-1003", 1, "large", ["vanilla"])]),
])
def test_grammar_reads_orders(app_modules, text, expected):
    _, _, voice = app_modules
    items, _ = voice.parse_items(voice.normalize(text))
    assert [(i["sku"], i["qty"], i["size"], i["mods"]) for i in items] == expected


def test_modifiers_an_item_cannot_take_are_dropped(app_modules):
    menu, _, voice = app_modules
    items, _ = voice.parse_items(voice.normalize("a muffin with oat milk and a latte"))
    assert [(i["sku"], i["mods"]) for i in items] == [("CF-2004", []), ("CF-1001", ["oat_milk"])]
    assert menu.normalize_line({"sku": "CF-2003", "size": "large", "mods": ["oat_milk"]}) == {
        "sku": "CF-2003", "qty": 1, "size": None, "mods": []}


# --------------------------------------------------------------------------
# voice ordering: drive-thru
# --------------------------------------------------------------------------
def test_recognized_guest_gets_usual_combo_offer_and_run_low_item(client):
    """Maya checks in at the lane: her usual, one merchandising offer at a time, and the item she is about to run out of."""
    s = lane(client, SALEM, shopper_id=MAYA, identified_by="app check-in")
    assert s["guest"]["first_name"] == "Maya" and "large oat milk Latte" in s["reply"] and s["pending"]["kind"] == "usual"
    s = say(client, s["session_id"], "yes please")
    assert {l["sku"] for l in s["cart"]} == {"CF-1001", "CF-2001"}
    latte = lines(s)["CF-1001"]
    # $5.95 menu price (large + oat milk), Plus 5% + morning coffee run 10% = 15% off
    assert latte["list_price"] == 5.95 and latte["unit_price"] == 5.06
    assert [a["rule"] for a in latte["adjustments"]] == ["tier", "time"]
    assert s["pending"]["offer"]["kind"] == "combo" and "breakfast combo" in s["reply"]
    before = s["totals"]["total_usd"]
    s = say(client, s["session_id"], "sure")
    assert "CF-2003" in lines(s) and all("bundle" in [a["rule"] for a in l["adjustments"]] for l in s["cart"])
    assert s["totals"]["total_usd"] - before < 0.25                 # the combo nearly pays for the hash browns
    # next offer: the nightly model says she is a day from running out of coffee pods, and one box is left
    assert s["pending"]["offer"]["kind"] == "run_low" and "Coffee Pods" in s["reply"] and "only 1 left" in s["reply"]
    s = say(client, s["session_id"], "yes")
    assert lines(s)[PODS_72]["made_to_order"] is False
    s = say(client, s["session_id"], "that's all")
    assert s["state"] == "confirming" and "Is that right?" in s["reply"] and "Plus pricing" in s["reply"]
    s = say(client, s["session_id"], "yes")
    assert s["state"] == "placed" and "Visa ending in 4417" in s["reply"] and "$12.50 store credit" in s["reply"]
    s = settled(client, f"/api/voice/session/{s['session_id']}")
    assert s["ticket"]["status"] == "confirmed" and s["ticket"]["order_id"].startswith("PS-")
    assert abs(s["ticket"]["total_usd"] - s["totals"]["total_usd"]) < 0.005   # the lane's price is the price charged


def test_lane_order_reaches_the_guests_phone(client):
    """What the lane sold shows up in the app: the order, the wallet, and a smart cart without what was just bought."""
    h = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"member_number": "204817", "last_name": "Chen"}).json()["token"]}
    docs = {(d["collection"], d["id"]): d["doc"] for d in client.get("/api/sync/pull", headers=h).json()["docs"]}
    me, recs = docs[("shoppers", f"shopper::{MAYA}")], docs[("recommendations", f"recs::{MAYA}")]
    order = next(o for o in me["order_history"] if o["order_id"].startswith("PS-"))
    assert order["channel"] == "drive_thru" and order["pickup_store"] == "ShopSmart Salem" and order["store_credit_applied_usd"] == 12.5
    assert {l["source"] for l in order["lines"]} == {"usual", "bundle", "smart_cart"}
    assert me["wallet"]["store_credit_usd"] == 0 and me["wallet"]["points"] > 1840
    assert PODS_72 not in {l["sku"] for l in recs["lines"] if l["selected"]}
    ticket = next(d for (col, key), d in docs.items() if col == "orders" and key.startswith("ticket::ST-0101::"))
    assert ticket["origin"] == "pos" and ticket["status"] == "confirmed" and ticket["title"].startswith("Drive-thru · Maya")


def test_walk_up_guest_then_member_number_reprices(client):
    s = lane(client, RHINEBECK)
    assert s["guest"] is None and "member number" in s["reply"]
    s = say(client, s["session_id"], "two large iced lattes with oat milk and a breakfast burrito, no cheese")
    assert [(l["sku"], l["qty"]) for l in s["cart"]] == [("CF-1001", 2), ("CF-2002", 1)]
    assert "2 large iced oat milk Lattes" in s["reply"]
    regular = s["totals"]["total_usd"]
    s = say(client, s["session_id"], "make that a small")
    assert lines(s)["CF-1001"]["size"] == "small" and s["totals"]["total_usd"] < regular
    s = say(client, s["session_id"], "actually take off the burrito")
    assert "CF-2002" not in lines(s)
    walk_up = s["totals"]["total_usd"]
    s = say(client, s["session_id"], "my member number is four two seven one nine zero")
    assert s["guest"]["first_name"] == "Sofia" and "Premier" in s["reply"]
    assert s["totals"]["total_usd"] < walk_up and s["totals"]["points_earned"] > 0     # 10% tier discount, 3 points per dollar
    s = say(client, s["session_id"], "sdkjfh qwe")
    assert s["trace"]["intent"] == "unknown" and "didn't catch" in s["reply"]


def test_kitchen_counts_drive_the_offer_and_the_86(client):
    """Smart merchandising at the lane follows the kitchen: an overbaked batch is marked down and offered, a sold-out item is refused."""
    store = "ST-0203"
    v = client.post(f"/api/pos/store/{store}/simulate", json={"action": "set_kitchen", "sku": "CF-2004", "value": 30}).json()
    assert next(k for k in v["kitchen"] if k["sku"] == "CF-2004")["overstock"] == 15
    board = {i["sku"]: i for i in client.get(f"/api/pos/store/{store}/menu").json()["items"]}
    assert board["CF-2004"]["list_price"] == 2.75 and board["CF-2004"]["price"] == 2.06      # 15% fresh batch + 10% morning
    s = lane(client, store)
    s = say(client, s["session_id"], "a medium latte")
    assert s["pending"]["offer"]["kind"] == "kitchen" and "muffin" in s["reply"]
    s = say(client, s["session_id"], "yes")
    assert "overstock" in [a["rule"] for a in lines(s)["CF-2004"]["adjustments"]]
    client.post(f"/api/pos/store/{store}/simulate", json={"action": "set_kitchen", "sku": "CF-4001", "value": 0})
    s = say(client, s["session_id"], "and two cookies")
    assert "sold out" in s["reply"] and "CF-4001" not in lines(s)


def test_phone_order_for_pickup(client):
    s = lane(client, RHINEBECK, channel="phone", shopper_id=DEVON, identified_by="caller ID")
    assert s["reply"].startswith("Hi Devon, thanks for calling ShopSmart Rhinebeck")
    s = say(client, s["session_id"], "no, just a turkey club no tomato, fries and an iced tea")
    assert {l["sku"] for l in s["cart"]} == {"CF-3002", "CF-3003", "CF-1004"}
    assert all("Lunch combo" in [a["label"] for a in l["adjustments"]] for l in s["cart"])
    s = say(client, s["session_id"], "that's it")
    s = say(client, s["session_id"], "yes")
    assert "ready at ShopSmart Rhinebeck" in s["reply"]
    s = settled(client, f"/api/voice/session/{s['session_id']}")
    assert s["ticket"]["status"] == "confirmed" and s["ticket"]["pickup_code"]


def test_lane_keeps_selling_when_the_wan_is_down(client):
    """With the store's link cut the agent still understands, prices and takes the order; the ticket waits at the store."""
    store = "ST-0204"
    assert client.post(f"/api/pos/store/{store}/simulate", json={"action": "wan_down"}).json()["wan"] == "down"
    s = lane(client, store)
    s = say(client, s["session_id"], "a large cold brew and a blueberry muffin")
    assert s["wan"] == "down" and s["trace"]["engine"] == "edge grammar" and len(s["cart"]) == 2
    s = say(client, s["session_id"], "no that's all")
    s = say(client, s["session_id"], "yes")
    assert s["state"] == "placed" and s["ticket"]["status"] == "on_edge" and s["ticket"]["queued_on_edge"]
    view = client.get(f"/api/pos/store/{store}").json()
    assert view["on_edge"] == 1 and view["tickets"][0]["kitchen"] == [{"qty": 1, "name": "large Cold Brew"}, {"qty": 1, "name": "Blueberry Muffin"}]
    time.sleep(0.5)
    assert client.get(f"/api/voice/session/{s['session_id']}").json()["ticket"]["status"] == "on_edge"   # the worker leaves it alone
    client.post(f"/api/pos/store/{store}/simulate", json={"action": "wan_up"})
    s = settled(client, f"/api/voice/session/{s['session_id']}")
    assert s["ticket"]["status"] == "confirmed" and s["ticket"]["queued_on_edge"]
    assert client.get(f"/api/pos/store/{store}").json()["metrics"]["tickets_queued_on_edge"] >= 1


# --------------------------------------------------------------------------
# self-checkout: recognition
# --------------------------------------------------------------------------
def test_vector_search_finds_the_item_and_its_lookalike(app_modules):
    _, vision, _ = app_modules
    vec, grams = vision.capture(PODS_120, "clear")
    top = vision.search(vec)
    assert [c["sku"] for c in top[:2]] == [PODS_120, PODS_72] and top[0]["score"] > 0.95
    assert top[0]["score"] - top[1]["score"] < vision.AUTO_MARGIN          # too close to call on the camera alone
    d = vision.decide(top, grams, None)
    assert d["action"] == "add" and d["sku"] == PODS_120 and d["method"] == "camera + scale"
    assert vision.decide(top, None, None)["action"] == "ask"               # no scale reading: the guest picks
    vec, grams = vision.capture(TOWELS, "clear")
    assert vision.decide(vision.search(vec), grams, None)["method"] == "camera"
    vec, grams = vision.capture("SS-330254", "poor")
    assert vision.decide(vision.search(vec), grams, None)["action"] == "scan"   # too little to go on: ask for the barcode


def test_every_item_is_recognized_without_a_barcode(app_modules):
    _, vision, _ = app_modules
    for sku, n in [(sku, n) for sku in vision.EMBEDDINGS for n in range(4)]:
        vec, grams = vision.capture(sku, "clear", n)
        d = vision.decide(vision.search(vec), grams, None)
        assert d["action"] == "add" and d["sku"] == sku, sku


def test_kiosk_recognizes_prices_and_suggests(client, app_modules):
    _, vision, _ = app_modules
    k = kiosk(client, "ST-0102", shopper_id=MAYA)
    assert k["guest"]["tier_name"] == "Plus" and k["index"]["items"] == len(vision.EMBEDDINGS)
    k = ev(client, k["session_id"], "place", sku=TOWELS)
    assert k["items"][0]["method"] == "camera" and k["last"]["action"] == "add"
    assert "overstock" in [a["rule"] for a in k["items"][0]["adjustments"]]
    bundle = next(s for s in k["suggestions"] if s["kind"] == "bundle")       # the engine's clear-out bundle, with the aisle
    assert "Trash Bags" in bundle["pitch"] and bundle["aisle"] and bundle["saves_usd"] > 0
    before = k["totals"]["subtotal_usd"]
    k = ev(client, k["session_id"], "add_suggestion", id=bundle["id"])
    assert TRASH_BAGS in lines(k) and "bundle" in [a["rule"] for a in lines(k)[TOWELS]["adjustments"]]
    assert round(k["totals"]["subtotal_usd"] - before, 2) == bundle["net_usd"]
    k = ev(client, k["session_id"], "place", sku="SS-440129", on_scale=False)      # a lookalike held in the hand, off the scale
    assert k["pending"]["kind"] == "which" and {"SS-440129", "SS-440145"} <= {o["sku"] for o in k["pending"]["options"]}
    assert client.post(f"/api/kiosk/session/{k['session_id']}/event", json={"kind": "pay", "data": {}}).status_code == 400
    k = ev(client, k["session_id"], "answer", choice="SS-440129")
    assert k["items"][-1]["method"] == "guest picked" and not k["pending"]
    k = ev(client, k["session_id"], "pay")
    k = settled(client, f"/api/kiosk/session/{k['session_id']}")
    assert k["state"] == "paid" and k["ticket"]["status"] == "confirmed" and k["ticket"]["points_earned"] > 0


def test_barcode_swap_is_caught_and_settled_by_the_attendant(client):
    k = kiosk(client, "ST-0103")
    k = ev(client, k["session_id"], "place", sku=EARBUDS_PRO, barcode=EARBUDS)     # the cheaper model's barcode
    exc = k["exceptions"][0]
    assert exc["kind"] == "scan_mismatch" and exc["scanned"] == EARBUDS and exc["seen"] == EARBUDS_PRO
    # $179.99 against the $122.99 the cheaper model sells at today (a competitor match)
    assert exc["scanned_price"] == 122.99 and exc["difference_usd"] == 57.0 and "attendant" in k["blockers"][0]
    r = client.post(f"/api/kiosk/session/{k['session_id']}/exceptions/{exc['id']}/resolve",
                    json={"decision": "charge_as_seen", "attendant": "Priya"})
    k = r.json()
    assert k["exceptions"][0]["status"] == "resolved" and k["exceptions"][0]["resolved_by"] == "Priya"
    assert k["items"][0]["sku"] == EARBUDS_PRO and k["totals"]["subtotal_usd"] == 179.99 and not k["blockers"]
    # an honest scan of a lookalike is not an exception
    k = ev(client, k["session_id"], "place", sku=PODS_72, barcode=PODS_72)
    assert len(k["exceptions"]) == 1 and k["items"][-1]["method"] == "barcode"


def test_missed_scan_is_a_reminder_first(client):
    k = kiosk(client, "ST-0104")
    k = ev(client, k["session_id"], "place", sku=TOWELS)
    k = ev(client, k["session_id"], "bag", sku="SS-110903")
    assert k["pending"]["kind"] == "missed" and "Trail Mix" in k["pending"]["ask"]
    k = ev(client, k["session_id"], "answer", choice="yes")
    assert k["items"][-1]["method"] == "bagging camera" and not k["exceptions"]           # no attendant needed
    k = ev(client, k["session_id"], "bag", sku="SS-990334")
    k = ev(client, k["session_id"], "answer", choice="no")
    assert k["exceptions"][0]["kind"] == "unscanned_item" and k["blockers"]
    k = client.post(f"/api/kiosk/session/{k['session_id']}/exceptions/{k['exceptions'][0]['id']}/resolve",
                    json={"decision": "remove"}).json()
    assert k["exceptions"][0]["status"] == "resolved" and "SS-990334" not in lines(k)


def test_purchase_limit_is_refused_at_the_kiosk_too(client):
    """The one-per-household rule on the Nova X is the same governed refusal in the store as in the app."""
    k = kiosk(client, PLANO, member_number="427190")
    k = ev(client, k["session_id"], "place", sku=CONSOLE)
    assert k["purchase_limits"][0]["already_bought"] == 1
    k = ev(client, k["session_id"], "place", sku="SS-110777")
    k = ev(client, k["session_id"], "pay")
    for _ in range(60):
        k = client.get(f"/api/kiosk/session/{k['session_id']}").json()
        if k["state"] == "scanning":
            break
        time.sleep(0.1)
    assert "limited to 1 per household" in k["error"]
    k = ev(client, k["session_id"], "remove", sku=CONSOLE)
    k = ev(client, k["session_id"], "pay")
    assert settled(client, f"/api/kiosk/session/{k['session_id']}")["state"] == "paid"


def test_a_phone_cannot_run_store_tools(client):
    """Tickets come only from the backend acting for a store device; a shopper's phone can't write one or run a store tool."""
    h = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"member_number": "318552", "last_name": "Walker"}).json()["token"]}
    doc = {"type": "request", "shopper_id": DEVON, "tool": "place_pos_order", "created_at": "2026-11-13T01:00:00Z",
           "args": {"store_id": RHINEBECK, "channel": "self_checkout", "lines": [{"sku": CONSOLE, "qty": 1}]}}
    r = client.post("/api/sync/push", headers=h, json={"docs": [{"collection": "orders", "id": f"order::{DEVON}::deadbeef0001",
                                                                "doc": {**doc, "origin": "pos", "store_id": RHINEBECK}}]})
    assert r.status_code == 403                               # the same refusal the Sync Gateway sync function gives
    oid = f"order::{DEVON}::deadbeef0002"
    assert client.post("/api/sync/push", headers=h, json={"docs": [{"collection": "orders", "id": oid, "doc": doc}]}).status_code == 200
    for _ in range(50):
        docs = {d["id"]: d["doc"] for d in client.get("/api/sync/pull", headers=h).json()["docs"]}
        if docs[oid]["status"] not in ("pending", "processing"):
            break
        time.sleep(0.1)
    assert docs[oid]["status"] == "failed" and "can't be requested from the app" in docs[oid]["summary"]


def test_store_metrics(client):
    m = client.get("/api/status").json()["metrics"]["pos"]
    assert m["orders_by_channel"]["drive_thru"] >= 2 and m["orders_by_channel"]["phone"] >= 1 and m["orders_by_channel"]["self_checkout"] >= 2
    assert m["voice_offers_accepted"] >= 3 and m["voice_avg_turn_ms"] is not None and m["voice_guests_recognized"] >= 3
    assert m["kiosk_scans_avoided"] >= 3 and m["kiosk_exceptions_resolved"] >= 2 and m["kiosk_shrink_prevented_usd"] >= 50
