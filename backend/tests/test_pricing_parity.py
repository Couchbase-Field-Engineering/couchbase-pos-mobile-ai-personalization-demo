"""The phone and the backend must price a cart identically, to the cent.

`mobile/src/pricing.ts` is a hand-written mirror of `app/pricing.py` (`quote`)
and `app/actions.py` (`_totals`). This test prices 1,200 random carts on the
backend, then runs the phone's code over the same carts in Node and compares
every line price, promotion and total. It needs Node and the app's
`node_modules` (for the TypeScript transform) and is skipped without them.
"""
import asyncio
import json
import os
import random
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MOBILE = os.path.join(ROOT, "mobile")
SUCRASE = os.path.join(MOBILE, "node_modules", ".bin", "sucrase")
CASES_PER_SHOPPER = 100


@pytest.mark.skipif(not shutil.which("node") or not os.path.exists(SUCRASE),
                    reason="needs Node and mobile/node_modules (run `npm install` in mobile/)")
def test_phone_prices_carts_exactly_like_the_backend(client, rows, tmp_path):
    from app import actions as A, catalog as C, pricing as P
    from app.docs import shopper_id_doc
    from app.store import store

    # The three demo shoppers plus nine others, covering every tier and kind of offer.
    by_tier: dict[str, list[str]] = {}
    for r in rows[3:]:
        ids = by_tier.setdefault(r["membership"]["tier"], [])
        if len(ids) < 3 and r["ml_scores"].get("offers"):
            ids.append(r["shopper_id"])
    shoppers = [r["shopper_id"] for r in rows[:3]] + [sid for ids in by_tier.values() for sid in ids]
    assert len(shoppers) == 12

    async def build():
        rnd = random.Random(7)
        cases = []
        (tmp_path / "catalog.json").write_text(json.dumps(await store.get("catalog", "catalog::v1")))
        for sid in shoppers:
            s = await store.get("shoppers", shopper_id_doc(sid))
            pricing = await P.get(s["delivery"]["fulfillment_center"])
            offers = A.shopper_offers(s)
            bundles = [b["skus"] for b in pricing["bundles"]]
            for _ in range(CASES_PER_SHOPPER):
                hour, option = rnd.randrange(24), rnd.choice(C.DELIVERY_OPTIONS)
                skus = rnd.sample([p["sku"] for p in C.PRODUCTS], rnd.randint(1, 7))
                if rnd.random() < 0.5:   # half the carts complete a bundle
                    skus = sorted(set(skus + rnd.choice(bundles)))
                cart = {k: rnd.choice([1, 1, 1, 2, 3, 4, 6, 7]) for k in skus}
                lines = P.quote(s["membership"]["tier"], cart, pricing, hour, option, offers)
                cases.append({"sid": sid, "hour": hour, "option": option, "cart": cart, "t": A._totals(s, lines, option),
                              "lines": {l["sku"]: [l["unit_price"], l["line_total"], l["savings"]] for l in lines}})
            (tmp_path / f"{sid}.json").write_text(json.dumps({"shopper": s, "pricing": pricing, "recs": {"offers": offers}}))
        (tmp_path / "cases.json").write_text(json.dumps(cases))
        return cases

    cases = asyncio.run(build())
    assert len(cases) == 1200
    assert any(c["t"]["bundles_applied"] for c in cases) and any(c["t"]["bonus_points"] for c in cases)

    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    shutil.copy(os.path.join(MOBILE, "src", "pricing.ts"), src / "pricing.ts")
    subprocess.run([SUCRASE, str(src), "-d", str(out), "--transforms", "typescript,imports"], check=True, capture_output=True)
    run = subprocess.run(["node", os.path.join(MOBILE, "scripts", "pricing-parity.js"), str(out / "pricing.js"), str(tmp_path)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert json.loads(run.stdout)["mismatches"] == 0
