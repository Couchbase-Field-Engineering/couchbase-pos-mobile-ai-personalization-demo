/**
 * Pricing on the phone. The same arithmetic as the backend
 * (backend/app/pricing.py `quote`, actions.py `_totals`), run against the
 * rule document Sync Gateway keeps in Couchbase Lite - so every price,
 * promotion, bundle and total is right with no signal, and the order carries
 * the rule version and hour it was priced at so the backend charges exactly
 * what the shopper saw.
 */
import type { Adjustment, Bundle, Catalog, Offer, PricingDoc, Product, RecsDoc, Shopper, TimeRule } from './types';

/** Round to cents the way Python's round(x, 2) does, so phone and server agree to the cent. */
function r2(n: number): number {
  // An exact half cent can only be an odd eighth (1546.00 x 6.25% tax = 96.625): Python rounds those to the even cent.
  if (Number.isInteger(n * 8) && !Number.isInteger(n * 4)) {
    const c = Math.floor(n * 100);
    return (c % 2 === 0 ? c : c + 1) / 100;
  }
  return Number(n.toFixed(2));
}

export type PriceCtx = {
  cat: Catalog | null;
  shopper: Shopper;
  pricing: PricingDoc | null;
  /** The hour time-of-day promotions see: the demo clock in the rule document, else this phone's clock. */
  hour: number;
  offers: Offer[];
  bySku: Map<string, Product>;
};

export function hourNow(pricing: PricingDoc | null): number {
  const demo = pricing?.clock?.demo_hour;
  if (demo != null) return ((Number(demo) % 24) + 24) % 24;
  // No demo clock: the wall clock in the retailer's pricing time zone, the same hour the backend sees.
  const tz = pricing?.clock?.tz;
  if (tz) {
    try {
      const h = Number(new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', hourCycle: 'h23' }).format(new Date()));
      if (Number.isInteger(h) && h >= 0 && h < 24) return h;
    } catch {
      /* no time-zone data on this device: fall back to its own clock */
    }
  }
  return new Date().getHours();
}

export function priceCtx(snap: { catalog: Catalog | null; shopper: Shopper | null; pricing: PricingDoc | null; recs: RecsDoc | null }): PriceCtx | null {
  if (!snap.shopper) return null;
  const used = new Set(snap.shopper.redeemed_offers ?? []);
  const offers = (snap.recs?.offers ?? snap.shopper.ml_scores.offers ?? []).filter((o) => !used.has(o.offer_id));
  return {
    cat: snap.catalog,
    shopper: snap.shopper,
    pricing: snap.pricing,
    hour: hourNow(snap.pricing),
    offers,
    bySku: new Map((snap.catalog?.products ?? []).map((p) => [p.sku, p])),
  };
}

export const windowActive = (w: TimeRule, hour: number) =>
  w.start_hour <= w.end_hour ? w.start_hour <= hour && hour < w.end_hour : hour >= w.start_hour || hour < w.end_hour;

function windowMatches(w: TimeRule, p: Product, option: string) {
  if (w.requires_option && w.requires_option !== option) return false;
  if (w.categories?.length && !w.categories.includes(p.category)) return false;
  if (w.tags?.length && !w.tags.some((t) => p.tags.includes(t))) return false;
  return true;
}

export const offerMatches = (o: Offer, p: Product) =>
  o.kind === 'category_pct' ? p.category === o.category : o.kind === 'sku_pct' ? p.sku === o.sku : false;

const best = <T extends { pct_off?: number }>(xs: T[]): T => xs.reduce((a, b) => ((b.pct_off ?? 0) > (a.pct_off ?? 0) ? b : a));

export type PricedLine = {
  sku: string;
  name: string;
  uom: string;
  qty: number;
  list_price: number;
  unit: number;
  total: number;
  savings: number;
  matched: boolean;
  /** Everything behind the price, in the order it is applied. */
  adjustments: Adjustment[];
  /** Why this item is left out of promotions (nearly sold out, high demand), if it is. */
  noPromo: string | null;
  product: Product;
};

/**
 * Price a cart. Member tier, multi-buy, overstock markdown, the hour, a
 * personal offer and a bundle are percentages off list, added together up to
 * the cap; then a competitor price cap; then an approved price match.
 */
export function quote(ctx: PriceCtx, cart: Record<string, number>, option = '', match?: { sku: string; price: number } | null): PricedLine[] {
  const { cat, shopper, pricing } = ctx;
  const tier = cat?.member_tiers.find((t) => t.code === shopper.membership.tier);
  const tierPct = tier?.discount_pct ?? shopper.membership.discount_pct;
  const tierName = tier?.name ?? shopper.membership.tier_name;
  const capPct = pricing?.max_discount_pct ?? cat?.max_discount_pct ?? 40;
  const multi = [...(cat?.multi_buy ?? [])].sort((a, b) => b.min_qty - a.min_qty);
  const bundles = (pricing?.bundles ?? []).filter((b) => b.active && b.skus.every((s) => (cart[s] ?? 0) >= 1));
  const lines: PricedLine[] = [];
  for (const [sku, qty] of Object.entries(cart)) {
    const p = ctx.bySku.get(sku);
    if (!p || qty < 1) continue;
    const rules = pricing?.sku_rules?.[sku] ?? {};
    const adj: Adjustment[] = [];
    if (tierPct) adj.push({ rule: 'tier', label: `${tierName} member`, pct: tierPct });
    const mb = multi.find((b) => qty >= b.min_qty);
    if (mb) adj.push({ rule: 'multi_buy', label: `Multi-buy ${mb.min_qty}+`, pct: mb.extra_pct });
    if (!rules.no_promo) {
      if (rules.overstock) adj.push({ rule: 'overstock', label: rules.overstock.label, pct: rules.overstock.pct_off });
      const windows = (pricing?.time_rules ?? []).filter((w) => windowActive(w, ctx.hour) && windowMatches(w, p, option));
      if (windows.length) {
        const w = best(windows);
        adj.push({ rule: 'time', id: w.id, label: w.label, pct: w.pct_off });
      }
      const mine = ctx.offers.filter((o) => offerMatches(o, p));
      if (mine.length) {
        const o = best(mine);
        adj.push({ rule: 'offer', id: o.offer_id, label: o.title, pct: o.pct_off ?? 0 });
      }
      const inB = bundles.filter((b) => b.skus.includes(sku));
      if (inB.length) {
        const b = best(inB);
        adj.push({ rule: 'bundle', id: b.id, label: `${b.name} bundle`, pct: b.pct_off });
      }
    }
    const pct = Math.min(capPct, adj.reduce((s, a) => s + (a.pct ?? 0), 0));
    let unit = r2(p.list_price * (1 - pct / 100));
    const cap = rules.no_promo ? undefined : rules.competitor?.price_cap;
    if (cap != null && cap < unit) {
      unit = cap;
      adj.push({ rule: 'competitor', label: rules.competitor!.label, price: cap });
    }
    const matched = !!match && match.sku === sku && match.price < unit;
    if (matched) {
      unit = match!.price;
      adj.push({ rule: 'price_match', label: 'Price match', price: unit });
    }
    lines.push({
      sku, name: p.name, uom: p.uom, qty, list_price: p.list_price, unit, total: r2(unit * qty), savings: r2((p.list_price - unit) * qty),
      matched, adjustments: adj, noPromo: rules.no_promo ? rules.no_promo.label : null, product: p,
    });
  }
  return lines;
}

/** The price of one item on its own (no bundle), at this hour, for this shopper. */
export function priceOne(ctx: PriceCtx, sku: string, qty = 1, option = ''): PricedLine | null {
  return quote(ctx, { [sku]: qty }, option)[0] ?? null;
}

/** The promotion behind a price, if any - what a badge on a product card says. */
export function dealOf(line: PricedLine | null): Adjustment | null {
  if (!line) return null;
  const order = ['competitor', 'overstock', 'offer', 'time', 'bundle'];
  return [...line.adjustments].filter((a) => order.includes(a.rule)).sort((a, b) => order.indexOf(a.rule) - order.indexOf(b.rule))[0] ?? null;
}

export const dealText = (a: Adjustment) => (a.rule === 'competitor' ? a.label : a.rule === 'offer' ? `Your offer · ${a.pct}% off` : `${a.label} · ${a.pct}% off`);

export type Totals = {
  lines: PricedLine[];
  subtotal: number;
  fee: number;
  tax: number;
  total: number;
  savings: number;
  storeCredit: number;
  due: number;
  points: number;
  bonusPoints: number;
  /** What each promotion took off this cart. */
  promotions: { label: string; saved: number }[];
  bundlesApplied: string[];
};

export function priceCart(ctx: PriceCtx, cart: Record<string, number>, option: string, match?: { sku: string; price: number } | null): Totals {
  const { cat, shopper } = ctx;
  const lines = quote(ctx, cart, option, match);
  const tier = cat?.member_tiers.find((t) => t.code === shopper.membership.tier);
  const subtotal = r2(lines.reduce((s, l) => s + l.total, 0));
  const pickup = option === (cat?.pickup_option ?? 'Store pickup');
  const fee = pickup || !tier || subtotal >= tier.free_delivery_min_usd || lines.length === 0 ? 0 : (cat?.delivery_fee_usd ?? 5.99);
  const tax = r2(subtotal * (cat?.tax_rate ?? 0.0625));
  const total = r2(subtotal + fee + tax);
  const storeCredit = r2(Math.min(shopper.wallet.store_credit_usd ?? 0, total));
  const bonusPoints = ctx.offers
    .filter((o) => o.kind === 'bonus_points' && subtotal >= (o.min_subtotal_usd ?? 0))
    .reduce((s, o) => s + (o.points ?? 0), 0);
  const applied = new Map<string, number>();
  for (const l of lines) {
    for (const a of l.adjustments) {
      if (['overstock', 'time', 'offer', 'bundle'].includes(a.rule) && a.pct != null) {
        applied.set(a.label, r2((applied.get(a.label) ?? 0) + ((l.list_price * a.pct) / 100) * l.qty));
      } else if (a.rule === 'competitor') applied.set(a.label, applied.get(a.label) ?? 0);
    }
  }
  return {
    lines, subtotal, fee, tax, total,
    savings: r2(lines.reduce((s, l) => s + l.savings, 0)),
    storeCredit,
    due: r2(total - storeCredit),
    points: Math.floor(subtotal) * (tier?.points_per_usd ?? 1) + bonusPoints,
    bonusPoints,
    promotions: [...applied].map(([label, saved]) => ({ label, saved })),
    bundlesApplied: [...new Set(lines.flatMap((l) => l.adjustments.filter((a) => a.rule === 'bundle').map((a) => a.id!)))].sort(),
  };
}

// ---------------------------------------------------------------------------
// merchandising: what to show next to the cart
// ---------------------------------------------------------------------------
export type BundleNudge = { bundle: Bundle; missing: Product[]; have: Product[]; saves: number };

/** What a bundle saves over buying the same items without it, at today's prices. */
export function bundleSaving(ctx: PriceCtx, b: Bundle, option = ''): number {
  const cart = Object.fromEntries(b.skus.map((s) => [s, 1]));
  const withB = quote(ctx, cart, option).reduce((s, l) => s + l.total, 0);
  const without = b.skus.reduce((s, sku) => s + (priceOne(ctx, sku, 1, option)?.total ?? 0), 0);
  return r2(without - withB);
}

/** Bundles the cart has started but not finished: the cross-sell with a number on it. */
export function bundleNudges(ctx: PriceCtx, cart: Record<string, number>, option = ''): BundleNudge[] {
  const out: BundleNudge[] = [];
  for (const b of ctx.pricing?.bundles ?? []) {
    if (!b.active) continue;
    const prods = b.skus.map((s) => ctx.bySku.get(s)).filter(Boolean) as Product[];
    const have = prods.filter((p) => cart[p.sku]);
    const missing = prods.filter((p) => !cart[p.sku]);
    if (!have.length || !missing.length || prods.length !== b.skus.length) continue;
    out.push({ bundle: b, have, missing, saves: bundleSaving(ctx, b, option) });
  }
  return out.sort((a, b) => a.missing.length - b.missing.length || b.saves - a.saves);
}

export type Suggestion = { product: Product; reason: string; because: Product; line: PricedLine };

/** Cross-sell: what sells with the items in the cart and is not in it yet. */
export function crossSell(ctx: PriceCtx, cart: Record<string, number>, skip: Set<string> = new Set()): Suggestion[] {
  const seen = new Set<string>();
  const out: Suggestion[] = [];
  for (const sku of Object.keys(cart)) {
    for (const x of ctx.cat?.pairs_with?.[sku] ?? []) {
      const product = ctx.bySku.get(x.sku);
      const because = ctx.bySku.get(sku);
      if (!product || !because || cart[x.sku] || seen.has(x.sku) || skip.has(x.sku)) continue;
      if (ctx.shopper.preferences.avoid_categories.includes(product.category)) continue;
      if ((ctx.pricing?.on_hand?.[x.sku] ?? 1) < 1) continue;
      seen.add(x.sku);
      out.push({ product, reason: x.reason, because, line: priceOne(ctx, x.sku)! });
    }
  }
  return out;
}

export type Upsell = { from: Product; to: Product; reason: string; line: PricedLine };

/** Upsell: the step-up for an item in the cart (a bigger pack, a better model), when it is in stock. */
export function upsells(ctx: PriceCtx, cart: Record<string, number>): Upsell[] {
  const out: Upsell[] = [];
  // Never trade a bundle the cart has already earned for a bigger pack.
  const bundled = new Set((ctx.pricing?.bundles ?? []).filter((b) => b.active && b.skus.every((s) => cart[s])).flatMap((b) => b.skus));
  for (const sku of Object.keys(cart)) {
    const u = ctx.cat?.upgrades?.[sku];
    const from = ctx.bySku.get(sku);
    const to = u ? ctx.bySku.get(u.sku) : undefined;
    if (!u || !from || !to || cart[u.sku] || bundled.has(sku) || (ctx.pricing?.on_hand?.[u.sku] ?? 1) < 1) continue;
    out.push({ from, to, reason: u.reason, line: priceOne(ctx, u.sku)! });
  }
  return out;
}

/** Days since an ISO date, measured from the export date (the demo's "today"). */
export function daysSince(iso: string | undefined, today: string): number | null {
  if (!iso) return null;
  return Math.round((Date.parse(today.slice(0, 10)) - Date.parse(iso.slice(0, 10))) / 86400000);
}

/** High-demand items carry a per-household limit over a rolling 30 days. */
export function overLimit(shopper: Shopper, product: Product, qty: number): { limit: number; bought: number } | null {
  const limit = product.limit_per_household;
  if (!limit) return null;
  let bought = 0;
  for (const o of shopper.order_history) {
    const d = daysSince(o.date, shopper.export_date);
    if (d != null && d <= 30) for (const l of o.lines) if (l.sku === product.sku) bought += l.qty;
  }
  return bought + qty > limit ? { limit, bought } : null;
}

export const hourLabel = (h: number) => `${((h + 11) % 12) + 1} ${h % 24 < 12 ? 'am' : 'pm'}`;
