import { Ionicons } from '@expo/vector-icons';
import { router, useLocalSearchParams } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useData } from '@/src/data/DataContext';
import { bundleSaving, hourLabel, overLimit, priceOne, quote } from '@/src/pricing';
import { theme } from '@/src/theme';
import { Button, Card, categoryIcon, Empty, fmtDate, GradientArt, money2, Pill, ProductThumb, SectionTitle, shortName, type IconName } from '@/src/ui/components';
import { openAction } from '@/src/ui/nav';

/** One product, rendered entirely from Couchbase Lite: how its price is built right now, stock, aisle, what goes with it, and why the model picked it. */
export default function ProductScreen() {
  const { sku = '', src = 'browse' } = useLocalSearchParams<{ sku: string; src?: string }>();
  const { snap, cart, addToCart, setQty, swapInCart, price } = useData();
  const cat = snap.catalog;
  const me = snap.shopper;
  const p = cat?.products.find((x) => x.sku === sku);
  if (!p || !me || !price) return <Empty icon="pricetag-outline" title="Not on this phone yet" body="The catalog syncs from Couchbase in a moment." />;

  const recs = snap.recs;
  const line = recs?.lines.find((l) => l.sku === sku);
  const pick = recs?.for_you.find((l) => l.sku === sku);
  const match = recs?.price_matches.find((m) => m.sku === sku);
  const onHand = snap.pricing?.on_hand?.[sku] ?? line?.on_hand_at_fc ?? pick?.on_hand_at_fc ?? null;
  // Priced with the rest of the cart, so a bundle this item completes shows in its price.
  const inCartNow = cart[sku] ?? 0;
  const priced = inCartNow ? quote(price, cart, me.delivery.default_option).find((l) => l.sku === sku)! : priceOne(price, sku, 1, me.delivery.default_option)!;
  const unit = priced.unit;
  const rules = snap.pricing?.sku_rules?.[sku] ?? {};
  const promos = priced.adjustments.filter((a) => a.rule !== 'tier' && a.rule !== 'multi_buy');
  const bundles = (snap.pricing?.bundles ?? []).filter((b) => b.skus.includes(sku) && b.skus.every((s) => price.bySku.has(s)));
  const pairs = (cat?.pairs_with?.[sku] ?? []).filter((x) => price.bySku.has(x.sku) && !me.preferences.avoid_categories.includes(price.bySku.get(x.sku)!.category));
  const up = cat?.upgrades?.[sku];
  const upP = up ? price.bySku.get(up.sku) : undefined;
  const upLine = upP ? priceOne(price, upP.sku) : null;
  const upInStock = upP ? (snap.pricing?.on_hand?.[upP.sku] ?? 1) > 0 : false;
  const window = promos.find((a) => a.rule === 'time');
  const windowRule = window ? snap.pricing?.time_rules.find((w) => w.id === window.id) : undefined;
  const aisle = snap.store?.aisles?.[sku];
  const rep = me.replenishment[sku];
  const inCart = cart[sku] ?? 0;
  const limit = overLimit(me, p, Math.max(1, inCart));
  const sub = me.subscriptions.find((s) => s.sku === sku);
  const fc = cat?.fulfillment_centers[me.delivery.fulfillment_center]?.name ?? me.delivery.fulfillment_center;
  const multi = [...(cat?.multi_buy ?? [])].sort((a, b) => a.min_qty - b.min_qty);

  return (
    <ScrollView contentContainerStyle={styles.body}>
      <Card>
        <GradientArt palette={p.category} icon={categoryIcon(p.category)} height={150}>
          <Pill label={cat?.categories[p.category] ?? p.category} tone="dark" />
          <Text style={styles.sku}>{p.sku}</Text>
        </GradientArt>
        <View style={{ padding: 16, gap: 8 }}>
          <Text style={styles.h2}>{p.name}</Text>
          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8 }}>
            <Text style={styles.price}>{money2(unit)}</Text>
            {unit < p.list_price ? <Text style={styles.was}>{money2(p.list_price)}</Text> : null}
            <Text style={styles.meta}>per {p.uom} · {p.pack}</Text>
          </View>
          <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
            {me.membership.discount_pct ? <Pill label={`${me.membership.tier_name} price · ${me.membership.discount_pct}% off`} tone={promos.length ? 'neutral' : 'red'} icon="ribbon-outline" /> : <Pill label="Member price" tone="neutral" />}
            {promos.map((a) => (
              <Pill key={a.rule} label={a.pct != null ? `${a.rule === 'offer' ? 'Your offer' : a.label} · ${a.pct}% off` : a.label} tone="red"
                icon={a.rule === 'offer' ? 'gift-outline' : a.rule === 'time' ? 'time-outline' : a.rule === 'bundle' ? 'albums-outline' : a.rule === 'competitor' ? 'pricetags-outline' : 'trending-down'} />
            ))}
            {rules.no_promo ? <Pill label={rules.no_promo.label} tone="warn" icon="flame-outline" /> : null}
            {p.limit_per_household ? <Pill label={`Limit ${p.limit_per_household} per household`} tone="warn" icon="alert-circle-outline" /> : null}
            {sub ? <Pill label={`Subscribed · every ${sub.cadence_weeks} wks`} tone="ok" icon="repeat" /> : null}
          </View>
          {inCart ? (
            <View style={styles.stepRow}>
              <Button label="" icon={inCart <= 1 ? 'trash-outline' : 'remove'} kind="ghost" small onPress={() => setQty(sku, inCart - 1)} />
              <Text style={styles.qty}>{inCart} in cart</Text>
              <Button label="" icon="add" kind="ghost" small onPress={() => setQty(sku, inCart + 1)} />
              <View style={{ flex: 1 }}><Button label="Go to cart" icon="bag-handle" onPress={() => router.replace('/(tabs)/cart')} /></View>
            </View>
          ) : (
            <Button label={`Add to cart · ${money2(unit)}`} icon="add" onPress={() => addToCart(sku, 1, src)} />
          )}
          {limit ? (
            <Text style={styles.warn}>
              Your household bought {limit.bought} in the last 30 days and the limit is {limit.limit}. An order with this item would be refused.
            </Text>
          ) : null}
        </View>
      </Card>

      {promos.length || rules.no_promo ? (
        <>
          <SectionTitle title="How this price is built" action={<Text style={styles.tiny}>rules v{snap.pricing?.version.slice(0, 6)} · on this phone</Text>} />
          <Card style={{ padding: 14, gap: 7 }}>
            <Step label="List price" value={money2(p.list_price)} />
            {priced.adjustments.map((a) => (
              <Step key={a.rule} label={a.rule === 'offer' ? `Your offer: ${a.label}` : a.label} value={a.pct != null ? `-${a.pct}%` : `capped at ${money2(a.price)}`} promo={a.rule !== 'tier' && a.rule !== 'multi_buy'} />
            ))}
            <View style={styles.stepTotal}>
              <Text style={{ fontWeight: '900', color: theme.color.ink }}>Your price</Text>
              <Text style={{ fontWeight: '900', color: theme.color.red, fontSize: theme.type.lg }}>{money2(unit)}</Text>
            </View>
            {rules.overstock ? <Text style={styles.meta}>Overstock markdown: {rules.overstock.reason}. It ends when stock is back to normal.</Text> : null}
            {windowRule ? <Text style={styles.meta}>{windowRule.reason}. Order before {hourLabel(windowRule.end_hour)} to keep it - the price is locked when you place the order, even offline.</Text> : null}
            {priced.adjustments.some((a) => a.rule === 'competitor') && rules.competitor ? <Text style={styles.meta}>{rules.competitor.reason}. No request needed.</Text> : null}
            {rules.no_promo ? <Text style={styles.warn}>{rules.no_promo.reason}. It is left out of promotions and bundles until stock recovers.</Text> : null}
          </Card>
        </>
      ) : null}

      {bundles.map((b) => {
        const others = b.skus.filter((s) => s !== sku);
        const missing = others.filter((s) => !cart[s]);
        const applied = priced.adjustments.some((a) => a.rule === 'bundle' && a.id === b.id);
        return (
          <Card key={b.id} style={[styles.match, !b.active && { borderColor: theme.color.border }]}>
            <Ionicons name="albums" size={22} color={b.active ? theme.color.red : theme.color.faint} />
            <View style={{ flex: 1, gap: 4 }}>
              <Text style={styles.matchTitle}>{b.name} bundle · {b.pct_off}% off each</Text>
              <Text style={styles.meta}>With {others.map((s) => shortName(price.bySku.get(s)!.name)).join(' and ')}. {b.reason}.</Text>
              {!b.active ? <Text style={styles.warn}>Paused: {b.paused_reason}.</Text>
                : applied ? <Pill label="Applied to your cart" tone="ok" icon="checkmark" />
                  : <Button small kind="dark" icon="add" label={`Add ${inCart ? 'the rest' : 'the bundle'} · save ${money2(bundleSaving(price, b))}`}
                      onPress={() => { if (!inCart) addToCart(sku, 1, 'bundle'); missing.forEach((s) => addToCart(s, 1, 'bundle')); }} />}
            </View>
          </Card>
        );
      })}

      {/* Not while this item is earning a bundle discount: swapping it out would lose the bundle. */}
      {upP && upLine && upInStock && !cart[upP.sku] && !priced.adjustments.some((a) => a.rule === 'bundle') ? (
        <Card style={styles.match}>
          <Ionicons name="arrow-up-circle" size={22} color={theme.color.red} />
          <View style={{ flex: 1, gap: 4 }}>
            <Text style={styles.matchTitle}>Step up: {shortName(upP.name)}</Text>
            <Text style={styles.meta}>{up!.reason}. {money2(upLine.unit)} for {upP.pack}.</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <Button small kind="dark" icon="swap-vertical" label={inCart ? 'Switch my cart to it' : 'Add it instead'} onPress={() => (inCart ? swapInCart(sku, upP.sku) : addToCart(upP.sku, 1, 'upsell'))} />
              <Button small kind="ghost" label="View" onPress={() => router.push({ pathname: '/product', params: { sku: upP.sku, src: 'upsell' } })} />
            </View>
          </View>
        </Card>
      ) : null}

      {match && match.competitor_price_usd < unit ? (
        <Card style={styles.match}>
          <Ionicons name="pricetags" size={22} color={theme.color.red} />
          <View style={{ flex: 1, gap: 2 }}>
            <Text style={styles.matchTitle}>{match.competitor} has it for {money2(match.competitor_price_usd)}</Text>
            <Text style={styles.meta}>Check out with a price match and save {money2(match.saving_usd)}. Customer Care approves it, then the order is placed.</Text>
          </View>
        </Card>
      ) : null}

      {pairs.length ? (
        <>
          <SectionTitle title="Goes well with" action={<Text style={styles.tiny}>from baskets like yours</Text>} />
          <Card style={{ padding: 4 }}>
            {pairs.map((x, i) => {
              const xp = price.bySku.get(x.sku)!;
              const xl = priceOne(price, x.sku)!;
              return (
                <Pressable key={x.sku} onPress={() => router.push({ pathname: '/product', params: { sku: x.sku, src: 'cross_sell' } })}
                  style={[styles.pair, i > 0 && { borderTopWidth: 1, borderTopColor: theme.color.border }]}>
                  <ProductThumb category={xp.category} size={40} />
                  <View style={{ flex: 1 }}>
                    <Text style={{ fontWeight: '800', color: theme.color.text, fontSize: theme.type.sm }} numberOfLines={1}>{shortName(xp.name)}</Text>
                    <Text style={styles.meta} numberOfLines={1}>{x.reason} · {money2(xl.unit)}</Text>
                  </View>
                  <Pressable onPress={() => addToCart(x.sku, 1, 'cross_sell')} disabled={!!cart[x.sku]} style={[styles.round, !!cart[x.sku] && { backgroundColor: theme.color.okSoft }]} accessibilityLabel={`Add ${shortName(xp.name)}`}>
                    <Ionicons name={cart[x.sku] ? 'checkmark' : 'add'} size={18} color={cart[x.sku] ? theme.color.ok : theme.color.onDark} />
                  </Pressable>
                </Pressable>
              );
            })}
          </Card>
        </>
      ) : null}

      <SectionTitle title="Where to get it" />
      <Card style={{ padding: 14, gap: 10 }}>
        <Row icon="cube-outline" label="Delivery" value={onHand == null ? `Ships from the ${fc}` : onHand > 0 ? `${onHand} on hand at the ${fc}` : `Out of stock at the ${fc} - it would backorder`} tone={onHand === 0 ? 'warn' : undefined} />
        {snap.store ? <Row icon="storefront-outline" label="In store" value={`${snap.store.name}${aisle ? ` · aisle ${aisle}` : ''} · ${snap.store.hours}`} /> : null}
        {snap.store ? <Row icon="time-outline" label="Pickup" value={`Usually ready in about ${Math.round(snap.store.pickup_ready_minutes / 30) / 2} hours, always free`} /> : null}
      </Card>

      {line || pick || rep ? (
        <>
          <SectionTitle title="Why you're seeing this" />
          <Card style={{ padding: 14, gap: 10 }}>
            {line ? <Row icon="hourglass-outline" label="Smart cart" value={`${line.reason}${line.confidence != null ? ` · ${Math.round(line.confidence * 100)}% confidence` : ''}`} /> : null}
            {pick ? <Row icon="sparkles-outline" label="Picked for you" value={`${pick.reason}${pick.confidence != null ? ` · ${Math.round(pick.confidence * 100)}% confidence` : ''}`} /> : null}
            {rep ? <Row icon="repeat-outline" label="Your history" value={`About ${rep.avg_monthly_qty} a month · last bought ${fmtDate(rep.last_ordered, { month: 'short', day: 'numeric' })}`} /> : null}
            <Text style={styles.tiny}>From model {recs?.model_version ?? 'shopsmart-next-basket'}, export {me.export_date}. Scored once in Databricks, served from this phone.</Text>
          </Card>
        </>
      ) : null}

      {multi.length ? (
        <>
          <SectionTitle title="Buy more, save more" />
          <Card style={{ padding: 14, gap: 6 }}>
            {multi.map((b) => (
              <View key={b.min_qty} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <Text style={styles.meta}>{b.min_qty}+ on one order</Text>
                <Text style={{ fontWeight: '800', color: theme.color.text }}>{money2(priceOne(price, sku, b.min_qty, me.delivery.default_option)!.unit)} each</Text>
              </View>
            ))}
          </Card>
        </>
      ) : null}

      {p.consumable && !sub ? (
        <View style={{ marginTop: 16 }}>
          <Button
            label={`Subscribe & Save · every 4 weeks`}
            icon="repeat"
            kind="dark"
            onPress={() => openAction({ label: 'Subscribe', tool: 'set_subscription', args: { sku, qty: Math.max(1, Number(rep?.last_qty ?? 1)), cadence_weeks: 4 } }, `Subscribe · ${shortName(p.name)}`)}
          />
        </View>
      ) : null}
    </ScrollView>
  );
}

function Step({ label, value, promo }: { label: string; value: string; promo?: boolean }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 12 }}>
      <Text style={[styles.meta, { flex: 1 }, promo && { color: theme.color.text, fontWeight: '700' }]}>{label}</Text>
      <Text style={{ fontWeight: '800', color: promo ? theme.color.red : theme.color.text }}>{value}</Text>
    </View>
  );
}

function Row({ icon, label, value, tone }: { icon: IconName; label: string; value: string; tone?: 'warn' }) {
  return (
    <View style={{ flexDirection: 'row', gap: 10, alignItems: 'flex-start' }}>
      <Ionicons name={icon} size={16} color={theme.color.red} style={{ marginTop: 1 }} />
      <Text style={[styles.meta, { width: 96 }]}>{label}</Text>
      <Text style={{ flex: 1, color: tone === 'warn' ? theme.color.warn : theme.color.text, fontWeight: '600', fontSize: theme.type.sm, lineHeight: 19 }}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center', paddingBottom: 40 },
  sku: { color: 'rgba(255,255,255,0.8)', fontSize: theme.type.xs, fontWeight: '700', letterSpacing: 1 },
  h2: { fontSize: theme.type.xl, fontWeight: '900', color: theme.color.ink, lineHeight: 25 },
  price: { fontSize: 28, fontWeight: '900', color: theme.color.ink },
  was: { color: theme.color.faint, fontSize: theme.type.md, textDecorationLine: 'line-through' },
  meta: { color: theme.color.muted, fontSize: theme.type.sm, lineHeight: 19 },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs, lineHeight: 16 },
  warn: { color: theme.color.warn, fontWeight: '700', fontSize: theme.type.sm, lineHeight: 19 },
  stepRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  qty: { fontWeight: '900', color: theme.color.ink, minWidth: 64, textAlign: 'center' },
  match: { flexDirection: 'row', gap: 12, alignItems: 'center', padding: 14, marginTop: 12, borderWidth: 1, borderColor: theme.color.redSoft },
  matchTitle: { fontWeight: '800', color: theme.color.ink, fontSize: theme.type.md },
  stepTotal: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', borderTopWidth: 1, borderTopColor: theme.color.border, paddingTop: 8, marginTop: 2 },
  pair: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 10 },
  round: { width: 32, height: 32, borderRadius: 16, backgroundColor: theme.color.red, alignItems: 'center', justifyContent: 'center' },
});
