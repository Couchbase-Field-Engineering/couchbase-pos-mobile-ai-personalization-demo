/**
 * Prices carts with the phone's pricing code and compares every number with
 * what the backend computed for the same cart. Run by
 * backend/tests/test_pricing_parity.py:
 *
 *   node pricing-parity.js <compiled pricing.js> <cases dir>
 *
 * The cases dir holds catalog.json, <shopper>.json ({shopper, pricing, recs})
 * and cases.json ([{sid, hour, option, cart, lines, t}]).
 */
const fs = require('fs');
const path = require('path');

const [, , compiled, dir] = process.argv;
const P = require(path.resolve(compiled));
const read = (f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
const cat = read('catalog.json');
const cases = read('cases.json');
const eq = (a, b) => Math.abs(a - b) < 1e-9;
const ctxs = {};
const bad = [];

for (const c of cases) {
  if (!ctxs[c.sid]) {
    const d = read(`${c.sid}.json`);
    ctxs[c.sid] = P.priceCtx({ catalog: cat, shopper: d.shopper, pricing: d.pricing, recs: d.recs });
  }
  const t = P.priceCart({ ...ctxs[c.sid], hour: c.hour }, c.cart, c.option);
  let ok =
    eq(t.subtotal, c.t.subtotal_usd) && eq(t.fee, c.t.delivery_fee_usd) && eq(t.tax, c.t.tax_usd) && eq(t.total, c.t.total_usd) &&
    eq(t.savings, c.t.savings_usd) && eq(t.storeCredit, c.t.store_credit_applied_usd) && eq(t.due, c.t.amount_due_usd) &&
    t.points === c.t.points_earned && t.bonusPoints === c.t.bonus_points &&
    JSON.stringify(t.bundlesApplied) === JSON.stringify(c.t.bundles_applied) &&
    JSON.stringify(t.promotions.map((p) => [p.label, p.saved])) === JSON.stringify(c.t.promotions.map((p) => [p.label, p.saved_usd])) &&
    t.lines.length === Object.keys(c.lines).length;
  for (const l of t.lines) {
    const e = c.lines[l.sku];
    if (!e || !eq(l.unit, e[0]) || !eq(l.total, e[1]) || !eq(l.savings, e[2])) ok = false;
  }
  if (!ok) bad.push({ sid: c.sid, hour: c.hour, option: c.option, cart: c.cart, server: c.t, phone: { subtotal: t.subtotal, tax: t.tax, total: t.total, due: t.due, points: t.points } });
}
console.log(JSON.stringify({ cases: cases.length, mismatches: bad.length, first: bad.slice(0, 3) }));
process.exit(bad.length ? 1 : 0);
