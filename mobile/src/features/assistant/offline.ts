/**
 * Offline assistant: answers from what Couchbase Lite already holds.
 *
 *  1. this shopper's earlier answers (`assistant` doc `answers`, keyed by the normalized question)
 *  2. their recommendations (`recommendations` doc), profile and home store
 *  3. their context cache (`context_cache` docs the server assembled from the
 *     Couchbase Labs Agent Operations Manager Knowledge Base + tool facts)
 *  4. the Shopping Guide FAQ synced in the `catalog` doc
 *
 * Mirrors backend/app/assistant.py's intent keywords so the same question
 * lands on the same cached context offline as online.
 */
import type { Snapshot } from '../../db/localDb';
import { ids } from '../../db/localDb';
import { bundleSaving, dealOf, hourLabel, overLimit, priceCtx, priceOne, windowActive } from '../../pricing';
import type { ChatMessage, Product, SuggestedAction } from '../../types';

const KEYWORDS: Record<string, string[]> = {
  deals: ['deals', 'deal', 'offer', 'offers', 'promo', 'promotion', 'on sale', 'sale today', 'bundle', 'bundles', 'coupon',
    'markdown', 'marked down', 'goes with', 'go with', 'pair with'],
  rewards: ['points', 'store credit', 'credit', 'wallet', 'rewards', 'membership', 'member', 'tier', 'plus', 'premier', 'balance', 'upgrade'],
  returns: ['return', 'refund', 'damaged', 'wrong item', 'broken', 'send back', 'goodwill', 'exchange', 'arrived'],
  subscription: ['subscribe', 'subscription', 'recurring', 'auto-ship', 'autoship', 'every week', 'every month', 'schedule', 'cadence'],
  availability: ['in stock', 'available', 'availability', 'on hand', 'backorder', 'out of', 'sold out', 'aisle', 'limit', 'limited',
    'more than one', 'second', 'where can i find', 'which aisle'],
  delivery: ['deliver', 'delivery', 'shipping', 'shipped', 'ships', 'arrive', ' eta', 'when will', 'pickup', 'pick up', 'same-day', 'delivery fee'],
  pricing: ['price', 'pricing', 'cost', 'how much', 'discount', 'cheaper', 'savings', 'price match', 'match', 'competitor', 'multi-buy'],
  history: ['history', 'last order', 'last time', 'previous', 'ordered before', 'usually', 'how often', 'spend', 'spent', 'what did i',
    'my order', 'track', 'order status', 'where is my'],
  preferences: ['preference', 'prefer', 'favorite', 'hide', 'stop showing', "don't show", 'not interested', 'more of', 'less of', 'substitut'],
  reorder: ['reorder', 're-order', 'running low', 'low on', 'run out', 'restock', 'smart cart', 'buy again', 'recommend', 'what should',
    'suggest', 'for me', 'need', 'cart', 'coffee', 'diapers', 'paper towels', 'detergent', 'dog food'],
};

const PROBLEM_WORDS = ['damaged', 'broken', 'wrong item', 'crushed', 'missing item', 'defective'];

export function classify(q: string): string {
  const m = q.toLowerCase();
  let best = 'general';
  let score = 0;
  for (const [intent, kws] of Object.entries(KEYWORDS)) {
    const s = kws.filter((k) => m.includes(k)).length;
    if (s > score) {
      best = intent;
      score = s;
    }
  }
  // Something went wrong with an item: that is a return or a credit, whatever else the sentence mentions.
  if (PROBLEM_WORDS.some((k) => m.includes(k))) return 'returns';
  return best;
}

export const normalize = (q: string) => q.toLowerCase().replace(/[^a-z0-9 ]/g, '').trim().slice(0, 120);
const money = (n: number) => `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const short = (n: string) => n.split(',')[0];
const GENERIC = new Set(['much', 'does', 'have', 'this', 'what', 'price', 'stock', 'with', 'that', 'there', 'where', 'find', 'pack', 'count',
  'second', 'another', 'more', 'than', 'cant', 'why', 'the', 'can', 'buy', 'how', 'for', 'and', 'set', 'kit', 'you', 'are']);

/** The catalog item a question names, by the most name/tag words matched. */
function findProduct(q: string, products: Product[]): Product | null {
  const words = q.toLowerCase().replace(/[^a-z ]/g, ' ').split(/\s+/).filter((w) => w.length >= 3 && !GENERIC.has(w));
  let best: Product | null = null;
  let top = 0;
  for (const p of products) {
    const hay = `${p.name} ${p.tags.join(' ')}`.toLowerCase();
    const s = words.filter((w) => hay.includes(w)).length;
    if (s > top) {
      best = p;
      top = s;
    }
  }
  return best;
}

export function answerOffline(q: string, snap: Snapshot): Omit<ChatMessage, 'id' | 'at' | 'role'> {
  let intent = classify(q);
  const prior = snap.chat?.answers?.[normalize(q)];
  if (prior) {
    return { text: prior.reply, intent, actions: prior.actions, offline: true, context: { status: 'device', latency_ms: 0 } };
  }
  const m = q.toLowerCase();
  const faq = (snap.catalog?.faq ?? [])
    .map((f) => ({ f, s: f.keywords.filter((k) => m.includes(k)).length }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)[0];

  const me = snap.shopper;
  const recs = snap.recs;
  const cat = snap.catalog;
  const product = cat ? findProduct(q, cat.products) : null;
  if (intent === 'general' && product) intent = 'availability';
  const personal = /\b(i|i'm|me|my|mine|we|our|us)\b/i.test(q) || intent === 'reorder' || intent === 'history' || !!product;
  const ctx = me ? snap.contexts[ids.ctx(me.shopper_id, personal ? intent : `guide_${intent}`)] : undefined;
  const actions: SuggestedAction[] = [];
  let text: string | null = null;
  let usedFaq = false;

  const price = priceCtx(snap);
  if (me && intent === 'deals' && price) {
    // Offers, the hour's promotion, markdowns and bundles, all from the pricing rules on this phone.
    const pairs = product ? (cat?.pairs_with?.[product.sku] ?? []).filter((x) => price.bySku.has(x.sku)) : [];
    if (product && /go(es)? (well )?with|pair|bundle/.test(m)) {
      const b = (snap.pricing?.bundles ?? []).find((x) => x.active && x.skus.includes(product.sku) && x.skus.every((s) => price.bySku.has(s)));
      text = pairs.length
        ? `With ${short(product.name)}, shoppers add ${pairs.slice(0, 2).map((x) => `${short(price.bySku.get(x.sku)!.name)} (${x.reason[0].toLowerCase()}${x.reason.slice(1)})`).join(' and ')}.`
        : `Nothing in particular sells with ${short(product.name)}.`;
      if (b) text += ` Put ${b.skus.map((s) => short(price.bySku.get(s)!.name)).join(' + ')} in the cart together and each is ${b.pct_off}% off (saves ${money(bundleSaving(price, b))}).`;
      pairs.slice(0, 2).forEach((x) => actions.push({ label: `View ${short(price.bySku.get(x.sku)!.name)}`, screen: 'product', args: { sku: x.sku, src: 'cross_sell' } }));
    } else {
      const parts: string[] = [];
      const o = price.offers[0];
      if (o) parts.push(`Your offer: ${o.title}. ${o.reason}${o.expires ? `; good until ${o.expires}` : ''}${price.offers.length > 1 ? `. You have ${price.offers.length - 1} more on the Home tab` : ''}.`);
      const w = (snap.pricing?.time_rules ?? []).find((x) => windowActive(x, price.hour));
      if (w) parts.push(`This hour, ${w.label}: ${w.reason} (until ${hourLabel(w.end_hour)}).`);
      const rules = snap.pricing?.sku_rules ?? {};
      const marked = Object.keys(rules)
        .filter((s) => rules[s].overstock && !rules[s].no_promo && price.bySku.has(s))
        .map((s) => priceOne(price, s)!)
        .filter((l) => dealOf(l));
      if (marked.length) parts.push(`Price drops at your fulfillment center: ${marked.slice(0, 3).map((l) => `${short(l.name)} ${money(l.unit)} (overstock markdown)`).join('; ')}.`);
      const bs = (snap.pricing?.bundles ?? []).filter((b) => b.active && b.skus.every((s) => price.bySku.has(s)));
      if (bs[0]) parts.push(`Bundle: ${bs[0].skus.map((s) => short(price.bySku.get(s)!.name)).join(' + ')} together are ${bs[0].pct_off}% off each (saves ${money(bundleSaving(price, bs[0]))})${bs.length > 1 ? `, and ${bs.length - 1} more bundles in the app` : ''}.`);
      text = parts.join(' ') || 'No promotions are running for you right now; every price in the app is already your member price.';
      if (marked[0]) actions.push({ label: `View ${short(marked[0].name)}`, screen: 'product', args: { sku: marked[0].sku, src: 'deal' } });
    }
    actions.push({ label: 'Open Cart', screen: 'cart' });
  } else if (me && personal) {
    const tier = me.membership.tier_name;
    if (recs && (intent === 'reorder' || (intent === 'subscription' && !product))) {
      const sel = recs.lines.filter((l) => l.selected);
      const urgent = recs.lines.filter((l) => l.days_to_runout != null && l.days_to_runout <= 7);
      const low = sel.filter((l) => l.on_hand_at_fc < l.qty);
      text = sel.length
        ? `From the forecast on this phone: your smart cart has ${sel.length} items, ${money(recs.suggested_total_usd)} before tax at your ${tier} price (saves ${money(recs.suggested_savings_usd)}).`
        : "You're well stocked according to this morning's forecast.";
      if (urgent.length) text += ` Most urgent: ${urgent.slice(0, 2).map((l) => `${short(l.name)} (~${Math.max(0, l.days_to_runout ?? 0)}d left)`).join(', ')}.`;
      if (low.length) text += ` Stock is short on ${low.slice(0, 2).map((l) => short(l.name)).join(', ')} - part may backorder.`;
      actions.push({ label: 'Review in Cart', screen: 'cart' });
    } else if ((intent === 'pricing' || intent === 'availability' || intent === 'subscription') && product && cat && price) {
      const line = priceOne(price, product.sku)!;
      const unit = line.unit;
      const deal = dealOf(line);
      const aisle = snap.store?.aisles?.[product.sku];
      const onHand = snap.pricing?.on_hand?.[product.sku] ?? [...(recs?.lines ?? []), ...(recs?.for_you ?? [])].find((l) => l.sku === product.sku)?.on_hand_at_fc;
      const known = onHand != null;
      text = `${product.name} is ${money(unit)} for you (${tier}, list ${money(product.list_price)}${deal ? `, including ${deal.label[0].toLowerCase()}${deal.label.slice(1)}` : ''})`;
      if (known) text += `; ${onHand} on hand at your fulfillment center as of the last sync`;
      if (aisle && snap.store) text += `${known ? ', and' : ';'} it's in aisle ${aisle} at ${snap.store.name}`;
      text += '.';
      const up = cat.upgrades?.[product.sku];
      const upP = up ? price.bySku.get(up.sku) : undefined;
      if (intent === 'pricing' && up && upP) text += ` Worth a look: ${short(upP.name)} - ${up.reason[0].toLowerCase()}${up.reason.slice(1)}.`;
      const pm = recs?.price_matches.find((p) => p.sku === product.sku);
      if (pm && pm.competitor_price_usd < unit) text += ` ${pm.competitor} has it for ${money(pm.competitor_price_usd)} - check out with a price match to save ${money(Math.round((unit - pm.competitor_price_usd) * 100) / 100)}.`;
      const lim = product.limit_per_household ? overLimit(me, product, 1) : null;
      if (product.limit_per_household) {
        text += ` It's limited to ${product.limit_per_household} per household every 30 days.`;
        if (lim) text += ' Your household already bought one in that window, so another order would be refused.';
      }
      if (intent === 'subscription' && product.consumable) text += ' It can go on Subscribe & Save - open it to set a schedule.';
      actions.push({ label: `View ${short(product.name)}`, screen: 'product', args: { sku: product.sku } });
    } else if (intent === 'rewards') {
      text = `You have ${money(me.wallet.store_credit_usd)} in store credit and ${me.wallet.points.toLocaleString('en-US')} points. ${tier} members get ${me.membership.discount_pct}% off every item` +
        (me.membership.renewal_date ? `; your membership renews ${me.membership.renewal_date}.` : '.');
    } else if (intent === 'history') {
      const lo = me.order_history[0];
      text = `You've spent ${money(me.spend.last_12mo_usd)} over ${me.spend.orders_last_12mo} orders in 12 months, about ${money(me.spend.avg_order_usd)} an order.` +
        (lo ? ` Last order ${lo.order_id} on ${lo.date} for ${money(lo.total_usd)}.` : '');
      actions.push({ label: 'See my orders', screen: 'orders' });
    } else if (intent === 'delivery') {
      text = `Your default is ${me.delivery.default_option} from the ${cat?.fulfillment_centers[me.delivery.fulfillment_center]?.name ?? me.delivery.fulfillment_center}.` +
        (snap.store ? ` Store pickup at ${snap.store.name} (${snap.store.hours}) is always free.` : '');
    } else if (intent === 'returns') {
      const lo = me.order_history[0];
      text = `Returns are accepted within 30 days and refunded to your store credit${lo ? `; your last order was ${lo.order_id} on ${lo.date}` : ''}. Start one from the Orders tab - it is saved on this phone and sent when you're back online. For a damaged delivery, ask me again with signal and I'll request a goodwill credit.`;
      actions.push({ label: 'See my orders', screen: 'orders' });
    } else if (intent === 'preferences') {
      const names = (codes: string[]) => codes.map((c) => cat?.categories[c] ?? c).join(', ');
      text = `You're seeing more of ${names(me.preferences.favorite_categories) || 'nothing in particular'} and hiding ${names(me.preferences.avoid_categories) || 'nothing'}.`;
      actions.push({ label: 'Edit my preferences', screen: 'preferences' });
    }
  }
  if (!text && faq) {
    text = faq.f.a;
    usedFaq = true;
  }
  if (!text && ctx?.kb_guide?.[0]) text = ctx.kb_guide[0].answer ?? ctx.kb_guide[0].content;
  if (!text) {
    text = "You're offline right now, so I can only answer from what's saved on this phone: your smart cart and picks, prices, your home store's aisles, your wallet and orders, and the Shopping Guide.";
  }
  return {
    text,
    intent,
    actions,
    offline: true,
    context: { status: ctx || recs ? 'device' : 'none', latency_ms: 0 },
    sources: usedFaq && faq ? [{ title: 'ShopSmart Shopping Guide (on device)', snippet: faq.f.q, kind: 'couchbase-lite' }] : [],
  };
}
