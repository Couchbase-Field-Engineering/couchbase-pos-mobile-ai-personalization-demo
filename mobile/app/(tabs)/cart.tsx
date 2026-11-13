import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useData } from '@/src/data/DataContext';
import { bundleNudges, crossSell, hourLabel, overLimit, priceCart, upsells, windowActive } from '@/src/pricing';
import { theme } from '@/src/theme';
import { ChoiceRow } from '@/src/ui/Choice';
import { Button, Card, Empty, money2, Pill, ProductThumb, SectionTitle, shortName, SyncBadge } from '@/src/ui/components';
import { OrderTracker } from '@/src/ui/OrderTracker';

/**
 * The cart. It starts as the model's smart cart, the shopper edits it, and
 * every number on it - member price, multi-buy, markdowns, the hour's
 * promotion, personal offers, bundles, tax, store credit, points - is
 * computed on the phone from the synced pricing rules, so checkout works
 * with no signal. It is also where the merchandising happens: finish a
 * bundle, add what goes with the cart, step up to a bigger pack.
 */
export default function Cart() {
  const { snap, sync, request, offline, cart, setQty, addToCart, swapInCart, sources, price, clearCart, refillCart } = useData();
  const insets = useSafeAreaInsets();
  const me = snap.shopper;
  const recs = snap.recs;
  const cat = snap.catalog;
  const [option, setOption] = useState<string>('');
  const [notes, setNotes] = useState('');
  const [reqId, setReqId] = useState<string | null>(null);
  const [wantMatch, setWantMatch] = useState(true);
  // What was just submitted, so a refused order can be put back in the cart.
  const [last, setLast] = useState<Record<string, number>>({});

  useEffect(() => {
    if (!option && me) setOption(me.delivery.default_option);
  }, [me, option]);

  if (!me || !price) return <Empty icon="bag-handle-outline" title="Loading your cart" body="Your smart cart syncs from Couchbase in a moment." />;

  // A price match applies to one item: the first one in the cart a competitor is beating.
  const opt = option || me.delivery.default_option;
  const plain = priceCart(price, cart, opt);
  // ...and only when today's price has not already dropped to or below the competitor's.
  const candidate = (recs?.price_matches ?? []).find((m) => cart[m.sku] && m.competitor_price_usd < (plain.lines.find((l) => l.sku === m.sku)?.unit ?? 0));
  const match = candidate && wantMatch ? { sku: candidate.sku, price: candidate.competitor_price_usd } : null;
  const t = match ? priceCart(price, cart, opt, match) : plain;
  const matchSaving = candidate ? Math.round((plain.lines.find((l) => l.sku === candidate.sku)!.unit - candidate.competitor_price_usd) * cart[candidate.sku] * 100) / 100 : 0;
  const stock = new Map<string, number>([...(recs?.lines ?? []), ...(recs?.for_you ?? [])].map((l) => [l.sku, l.on_hand_at_fc]));
  const short = t.lines.filter((l) => stock.has(l.sku) && (stock.get(l.sku) ?? 0) < l.qty);
  const limited = t.lines.map((l) => ({ l, over: overLimit(me, l.product, l.qty) })).filter((x) => x.over);
  const alsoLow = (recs?.lines ?? []).filter((l) => !cart[l.sku]);
  // Merchandising, computed against this cart on the phone.
  const nudges = bundleNudges(price, cart, opt).slice(0, 2);
  const nudged = new Set(nudges.flatMap((n) => n.missing.map((p) => p.sku)));
  const ups = upsells(price, cart).slice(0, 2);
  const cross = crossSell(price, cart, nudged).slice(0, 3);
  const shown = new Set([...nudged, ...cross.map((c) => c.product.sku), ...ups.map((u) => u.to.sku)]);
  const picks = (recs?.for_you ?? []).filter((l) => !cart[l.sku] && !shown.has(l.sku)).slice(0, 2);
  // A promotion this cart is one step away from.
  const pickupDeal = (snap.pricing?.time_rules ?? []).find((w) => windowActive(w, price.hour) && w.requires_option && w.requires_option !== opt);
  const nextBonus = price.offers.find((o) => o.kind === 'bonus_points' && t.subtotal < (o.min_subtotal_usd ?? 0) && (o.min_subtotal_usd ?? 0) - t.subtotal <= 40);
  const tier = cat?.member_tiers.find((x) => x.code === me.membership.tier);
  const pickup = option === (cat?.pickup_option ?? 'Store pickup');

  const submit = async () => {
    const lines = t.lines.map((l) => ({ sku: l.sku, qty: l.qty }));
    // The rule version and hour this cart was priced at: the backend charges exactly these prices,
    // even if a promotion ends before the order syncs. `sources` is what led to each item being added.
    const base = {
      lines, delivery_option: option, notes: notes.trim(),
      ...(snap.pricing ? { pricing_version: snap.pricing.version, priced_hour: price.hour } : {}),
      sources: Object.fromEntries(t.lines.map((l) => [l.sku, sources[l.sku] ?? 'browse'])),
    };
    const req = match && candidate
      ? await request('request_price_match', { ...base, sku: candidate.sku, competitor: candidate.competitor, competitor_price_usd: candidate.competitor_price_usd },
          `Order with price match · ${money2(t.due)}`)
      : await request('place_order', base, `Order · ${t.lines.length} item${t.lines.length > 1 ? 's' : ''} · ${money2(t.due)}`);
    setLast(cart);
    setReqId(req._id);
    clearCart();
    setNotes('');
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.bg }}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.h1}>Cart</Text>
          <Text style={styles.sub} numberOfLines={1}>{me.membership.tier_name} prices · ships from {cat?.fulfillment_centers[me.delivery.fulfillment_center]?.name ?? me.delivery.fulfillment_center}</Text>
        </View>
        <SyncBadge sync={sync} />
      </View>
      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        {reqId ? (
          <OrderTracker
            id={reqId}
            onRetry={() => {
              Object.entries(last).forEach(([sku, qty]) => setQty(sku, qty));
              setReqId(null);
            }}
            onDone={() => setReqId(null)}
          />
        ) : null}

        {!reqId && t.lines.length === 0 ? (
          <>
            <Empty icon="bag-handle-outline" title="Your cart is empty" body="Start from the smart cart the model built for you, or browse the Shop tab." />
            <View style={{ gap: 8 }}>
              {recs?.lines.some((l) => l.selected) ? <Button label="Fill with my smart cart" icon="sparkles" onPress={refillCart} /> : null}
              <Button label="Browse the shop" kind="ghost" icon="search" onPress={() => router.push('/(tabs)/shop')} />
            </View>
          </>
        ) : null}

        {!reqId && t.lines.length ? (
          <>
            <SectionTitle title={`Items · ${t.lines.length}`} action={<Text style={styles.prov}>started from your smart cart</Text>} />
            <Card style={{ padding: 4 }}>
              {t.lines.map((l, i) => {
                const onHand = stock.get(l.sku);
                return (
                  <View key={l.sku} style={[styles.line, i > 0 && styles.divider]}>
                    <Pressable onPress={() => router.push({ pathname: '/product', params: { sku: l.sku } })}><ProductThumb category={l.product.category} size={42} /></Pressable>
                    <Pressable style={{ flex: 1, gap: 2 }} onPress={() => router.push({ pathname: '/product', params: { sku: l.sku } })}>
                      <Text style={styles.lineName} numberOfLines={2}>{shortName(l.name)}</Text>
                      <Text style={styles.meta}>
                        {money2(l.unit)} each{l.unit < l.list_price ? ` (list ${money2(l.list_price)})` : ''}
                      </Text>
                      {l.adjustments.filter((a) => a.rule !== 'tier' && a.rule !== 'price_match').map((a) => (
                        <Text key={a.rule} style={styles.promo}>
                          {a.rule === 'offer' ? `Your offer · ${a.pct}% off` : a.rule === 'overstock' ? `Markdown · ${a.pct}% off` : a.pct != null ? `${a.label} · ${a.pct}% off` : a.label}
                        </Text>
                      ))}
                      {l.noPromo ? <Text style={[styles.meta, { color: theme.color.warn, fontWeight: '700' }]}>{l.noPromo} - not in promotions</Text> : null}
                      {l.matched ? <Text style={[styles.meta, { color: theme.color.red, fontWeight: '700' }]}>Price match requested</Text> : null}
                      {onHand != null && onHand < l.qty ? (
                        <Text style={[styles.meta, { color: theme.color.warn, fontWeight: '700' }]}>
                          {onHand ? `Only ${onHand} on hand - ${l.qty - onHand} will backorder` : 'Out of stock - this will backorder'}
                        </Text>
                      ) : null}
                    </Pressable>
                    <View style={styles.stepper}>
                      <Pressable onPress={() => setQty(l.sku, l.qty - 1)} style={styles.step}><Ionicons name={l.qty <= 1 ? 'trash-outline' : 'remove'} size={15} color={theme.color.ink} /></Pressable>
                      <Text style={styles.qty}>{l.qty}</Text>
                      <Pressable onPress={() => setQty(l.sku, l.qty + 1)} style={styles.step}><Ionicons name="add" size={15} color={theme.color.ink} /></Pressable>
                    </View>
                    <Text style={styles.lineTotal}>{money2(l.total)}</Text>
                  </View>
                );
              })}
            </Card>

            {candidate ? (
              <Card style={styles.match}>
                <Ionicons name="pricetags" size={22} color={theme.color.red} />
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={styles.matchTitle}>Price match: save {money2(matchSaving)}</Text>
                  <Text style={styles.meta}>{candidate.competitor} has {shortName(candidate.name)} for {money2(candidate.competitor_price_usd)}. Customer Care approves the match before the order is placed.</Text>
                </View>
                <Switch value={wantMatch} onValueChange={setWantMatch} trackColor={{ true: theme.color.red, false: theme.color.border }} thumbColor={theme.color.surface} {...({ activeThumbColor: theme.color.surface } as object)} />
              </Card>
            ) : null}

            {nudges.length || ups.length || cross.length || alsoLow.length || picks.length ? (
              <>
                <SectionTitle title="Add before you check out" action={<Text style={styles.prov}>based on this cart</Text>} />
                <View style={{ gap: 8 }}>
                  {nudges.map((n) => (
                    <Pressable key={n.bundle.id} onPress={() => n.missing.forEach((p) => addToCart(p.sku, 1, 'bundle'))}>
                      <Card style={[styles.sugg, styles.nudge]}>
                        <Ionicons name="albums" size={20} color={theme.color.red} />
                        <View style={{ flex: 1 }}>
                          <Text style={styles.lineName} numberOfLines={2}>Add {n.missing.map((p) => shortName(p.name)).join(' + ')}</Text>
                          <Text style={styles.meta} numberOfLines={2}>
                            Completes the {n.bundle.name} bundle with your {n.have.map((p) => shortName(p.name)).join(' and ')}: {n.bundle.pct_off}% off each
                          </Text>
                        </View>
                        <Pill label={`Save ${money2(n.saves)}`} tone="red" />
                      </Card>
                    </Pressable>
                  ))}
                  {ups.map((u) => (
                    <Pressable key={u.to.sku} onPress={() => swapInCart(u.from.sku, u.to.sku)}>
                      <Card style={styles.sugg}>
                        <Ionicons name="arrow-up-circle" size={20} color={theme.color.red} />
                        <View style={{ flex: 1 }}>
                          <Text style={styles.lineName} numberOfLines={2}>{shortName(u.to.name)}: step up to {u.to.pack}</Text>
                          <Text style={styles.meta} numberOfLines={2}>{u.reason} · {money2(u.line.unit)} · replaces the one in your cart</Text>
                        </View>
                        <Pill label="Step up" tone="info" />
                      </Card>
                    </Pressable>
                  ))}
                  {cross.map((c) => (
                    <Pressable key={c.product.sku} onPress={() => addToCart(c.product.sku, 1, 'cross_sell')}>
                      <Card style={styles.sugg}>
                        <Ionicons name="add-circle" size={20} color={theme.color.red} />
                        <View style={{ flex: 1 }}>
                          <Text style={styles.lineName} numberOfLines={1}>{shortName(c.product.name)}</Text>
                          <Text style={styles.meta} numberOfLines={1}>{c.reason} · {money2(c.line.unit)}</Text>
                        </View>
                        <Pill label="Goes with" tone="neutral" />
                      </Card>
                    </Pressable>
                  ))}
                  {alsoLow.map((l) => (
                    <Pressable key={l.sku} onPress={() => addToCart(l.sku, l.qty, 'smart_cart')}>
                      <Card style={styles.sugg}>
                        <Ionicons name="add-circle" size={20} color={theme.color.red} />
                        <View style={{ flex: 1 }}>
                          <Text style={styles.lineName} numberOfLines={1}>{l.qty} × {shortName(l.name)}</Text>
                          <Text style={styles.meta} numberOfLines={1}>{l.reason} · {money2(l.unit_price)}</Text>
                        </View>
                        <Pill label="Running low" tone="warn" />
                      </Card>
                    </Pressable>
                  ))}
                  {picks.map((l) => (
                    <Pressable key={l.sku} onPress={() => addToCart(l.sku, 1, 'pick')}>
                      <Card style={styles.sugg}>
                        <Ionicons name="sparkles" size={18} color={theme.color.info} />
                        <View style={{ flex: 1 }}>
                          <Text style={styles.lineName} numberOfLines={1}>{shortName(l.name)}</Text>
                          <Text style={styles.meta} numberOfLines={1}>{l.reason} · {money2(l.unit_price)}</Text>
                        </View>
                        <Pill label="For you" tone="info" />
                      </Card>
                    </Pressable>
                  ))}
                </View>
              </>
            ) : null}

            <SectionTitle title="Delivery" />
            <Card style={{ padding: 14, gap: 10 }}>
              <ChoiceRow options={(cat?.delivery_options ?? ['Same-day delivery', '2-day shipping', 'Store pickup']).map((w) => ({ value: w, label: w }))} value={option} onChange={setOption} />
              <Text style={styles.meta}>
                {pickup
                  ? `${snap.store?.name ?? me.home_store?.name ?? 'Your store'} · ${snap.store?.hours ?? ''} · free`
                  : `${me.address.street}, ${me.address.city}${me.delivery.notes ? ` · ${me.delivery.notes}` : ''}`}
              </Text>
              {pickupDeal ? (
                <Pressable onPress={() => setOption(pickupDeal.requires_option!)} style={styles.hint}>
                  <Ionicons name="trending-up" size={16} color={theme.color.red} />
                  <Text style={styles.hintText}>{pickupDeal.label}: it's peak delivery time. Switch to {pickupDeal.requires_option} and take {pickupDeal.pct_off}% off everything until {hourLabel(pickupDeal.end_hour)}. Tap to switch.</Text>
                </Pressable>
              ) : null}
              <TextInput value={notes} onChangeText={setNotes} placeholder="Notes for the driver or store (optional)" placeholderTextColor={theme.color.faint} style={styles.field} />
            </Card>

            <SectionTitle title="Total" />
            <Card style={{ padding: 14, gap: 6 }}>
              <Tot label="Subtotal" v={t.subtotal} />
              {t.promotions.map((p) => (
                <View key={p.label} style={styles.tot}>
                  <Text style={[styles.promo, { flex: 1, marginRight: 10 }]} numberOfLines={1}>{p.label}</Text>
                  <Text style={styles.promo}>{p.saved ? `included · -${money2(p.saved)}` : 'included'}</Text>
                </View>
              ))}
              <Tot label={t.fee ? 'Delivery' : pickup ? 'Pickup (free)' : tier && tier.free_delivery_min_usd === 0 ? `Delivery (free with ${tier.name})` : 'Delivery (free over $35)'} v={t.fee} />
              <Tot label="Est. tax" v={t.tax} />
              {t.storeCredit ? <Tot label="Store credit" v={-t.storeCredit} ok /> : null}
              <View style={[styles.tot, styles.totLine]}>
                <Text style={{ fontWeight: '900', color: theme.color.ink, fontSize: theme.type.lg }}>You pay</Text>
                <Text style={{ fontWeight: '900', color: theme.color.red, fontSize: theme.type.xl }}>{money2(t.due)}</Text>
              </View>
              <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
                <Pill label={`You save ${money2(t.savings)}`} tone="ok" icon="pricetag-outline" />
                <Pill label={`Earns ${t.points.toLocaleString('en-US')} pts`} tone="neutral" icon="star-outline" />
                {t.bonusPoints ? <Pill label={`Includes ${t.bonusPoints} bonus pts`} tone="red" icon="gift-outline" /> : null}
                {short.length ? <Pill label={`${short.length} item${short.length > 1 ? 's' : ''} short`} tone="warn" icon="cube-outline" /> : null}
                {offline ? <Pill label="Will send when connected" tone="warn" icon="cloud-upload-outline" /> : null}
              </View>
              {nextBonus ? (
                <Text style={styles.promo}>Add {money2((nextBonus.min_subtotal_usd ?? 0) - t.subtotal)} more to earn {nextBonus.points} bonus points ({nextBonus.title.toLowerCase()}).</Text>
              ) : null}
              {limited.length ? (
                <>
                  <Text style={styles.warn}>
                    {shortName(limited[0].l.name)} is limited to {limited[0].over!.limit} per household every 30 days and your household already bought {limited[0].over!.bought}. This order would be refused.
                  </Text>
                  <Button label={`Remove ${shortName(limited[0].l.name)}`} icon="trash-outline" onPress={() => setQty(limited[0].l.sku, 0)} />
                  <Button label="Place the order anyway" kind="ghost" onPress={() => void submit()} />
                </>
              ) : match ? (
                <Button label={`Place order with price match · ${money2(t.due)}`} icon="pricetags" onPress={() => void submit()} />
              ) : (
                <Button label={`Place order · ${money2(t.due)}`} icon="bag-check" onPress={() => void submit()} />
              )}
              <Text style={styles.tiny}>
                Priced on this phone from your {me.membership.tier_name} tier and pricing rules v{snap.pricing?.version.slice(0, 6) ?? '-'} at {hourLabel(price.hour)}; that price is locked in when you place the order. Paying with {me.wallet.payment_method}. The order is saved in Couchbase Lite and sent
                through Sync Gateway when you have a connection; Couchbase Labs Agent Operations Manager runs it as a governed tool call.
              </Text>
            </Card>
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}

function Tot({ label, v, ok }: { label: string; v: number; ok?: boolean }) {
  return (
    <View style={styles.tot}>
      <Text style={styles.meta}>{label}</Text>
      <Text style={{ fontWeight: '700', color: ok ? theme.color.ok : theme.color.text }}>{v < 0 ? `-${money2(-v)}` : money2(v)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, paddingHorizontal: 16, paddingBottom: 12, backgroundColor: theme.color.surface, borderBottomWidth: 1, borderBottomColor: theme.color.border },
  h1: { fontSize: theme.type.title, fontWeight: '900', color: theme.color.ink },
  sub: { color: theme.color.muted, marginTop: 2, fontSize: theme.type.sm },
  body: { paddingHorizontal: 16, maxWidth: 640, width: '100%', alignSelf: 'center', paddingBottom: 40 },
  meta: { color: theme.color.muted, fontSize: theme.type.sm, lineHeight: 18 },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs, textAlign: 'center', lineHeight: 16, marginTop: 4 },
  warn: { color: theme.color.warn, fontWeight: '700', fontSize: theme.type.sm, lineHeight: 19 },
  prov: { color: theme.color.muted, fontSize: theme.type.xs, fontWeight: '600' },
  line: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 10 },
  divider: { borderTopWidth: 1, borderTopColor: theme.color.border },
  lineName: { fontWeight: '700', color: theme.color.text, fontSize: theme.type.sm },
  lineTotal: { fontWeight: '800', color: theme.color.text, width: 62, textAlign: 'right', fontSize: theme.type.sm },
  stepper: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  step: { width: 28, height: 28, borderRadius: 14, backgroundColor: theme.color.bg, alignItems: 'center', justifyContent: 'center' },
  qty: { width: 22, textAlign: 'center', fontWeight: '900', color: theme.color.ink },
  match: { flexDirection: 'row', gap: 12, alignItems: 'center', padding: 14, marginTop: 12, borderWidth: 1, borderColor: theme.color.redSoft },
  matchTitle: { fontWeight: '800', color: theme.color.ink, fontSize: theme.type.md },
  sugg: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12 },
  nudge: { borderWidth: 1, borderColor: theme.color.redSoft },
  promo: { color: theme.color.redDark, fontSize: theme.type.sm, fontWeight: '700', lineHeight: 18 },
  hint: { flexDirection: 'row', gap: 8, alignItems: 'flex-start', backgroundColor: theme.color.redSoft, borderRadius: theme.radiusSm, padding: 10 },
  hintText: { flex: 1, color: theme.color.redDark, fontSize: theme.type.sm, fontWeight: '600', lineHeight: 18 },
  field: { borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radiusSm, padding: 11, fontSize: theme.type.md, color: theme.color.text },
  tot: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  totLine: { borderTopWidth: 1, borderTopColor: theme.color.border, paddingTop: 8, marginTop: 2 },
});
