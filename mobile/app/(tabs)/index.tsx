import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { router } from 'expo-router';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useData } from '@/src/data/DataContext';
import { bundleSaving, dealOf, dealText, hourLabel, priceCart, priceOne, windowActive, type PriceCtx } from '@/src/pricing';
import { theme } from '@/src/theme';
import type { Bundle, CartLine, Offer, Pick, Product } from '@/src/types';
import { Brand, Button, Card, Empty, fmtDate, money2, Pill, ProductThumb, SectionTitle, shortName, STATUS_UI, SyncBadge } from '@/src/ui/components';

const openProduct = (sku: string, src = 'browse') => router.push({ pathname: '/product', params: { sku, src } });

export default function Home() {
  const { snap, sync, refresh, cart, cartCount, addToCart, price } = useData();
  const insets = useSafeAreaInsets();
  const me = snap.shopper;
  const recs = snap.recs;
  const recent = snap.orders.slice(0, 3);
  // The pricing clock when the rules carry one, so the greeting and the hour's promotion agree.
  const hour = price?.hour ?? new Date().getHours();
  const greet = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const lines = recs?.lines ?? [];
  const totals = price ? priceCart(price, cart, price.shopper.delivery.default_option) : null;
  const hidden = new Set(me?.preferences.avoid_categories ?? []);
  const rules = snap.pricing?.sku_rules ?? {};
  // What the pricing engine is doing at this shopper's fulfillment center right now.
  const windows = (snap.pricing?.time_rules ?? []).filter((w) => price && windowActive(w, price.hour));
  const marked = price
    ? Object.keys(rules)
        .filter((sku) => !rules[sku].no_promo && (rules[sku].overstock || rules[sku].competitor))
        .map((sku) => ({ p: price.bySku.get(sku), line: priceOne(price, sku) }))
        .filter((x): x is { p: Product; line: NonNullable<ReturnType<typeof priceOne>> } => !!x.p && !!x.line && !hidden.has(x.p.category) && !!dealOf(x.line))
        .sort((a, b) => b.line.savings / b.line.list_price - a.line.savings / a.line.list_price)
        .slice(0, 8)
    : [];
  const mine = (b: Bundle) => b.skus.filter((s) => me?.replenishment[s] || cart[s]).length;
  const bundles = price
    ? (snap.pricing?.bundles ?? [])
        .filter((b) => b.active && b.skus.every((s) => price.bySku.has(s)) && !b.skus.some((s) => hidden.has(price.bySku.get(s)!.category)))
        .sort((a, b) => mine(b) - mine(a) || Number(b.kind === 'dynamic') - Number(a.kind === 'dynamic'))
        .slice(0, 3)
    : [];
  const noSignal = me && !/good signal/i.test(me.engagement.connectivity_note ?? '');

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.bg }}>
      <View style={[styles.topbar, { paddingTop: insets.top + 8 }]}>
        <Brand size={19} />
        <Pressable onPress={() => router.push('/profile')} style={styles.avatar} accessibilityLabel="Account">
          <Text style={styles.avatarText}>{me ? `${me.first_name[0]}${me.last_name[0]}` : '··'}</Text>
        </Pressable>
      </View>
      <ScrollView refreshControl={<RefreshControl refreshing={false} onRefresh={() => void refresh()} />}>
        <View style={styles.body}>
          <LinearGradient colors={theme.gradient.hero as unknown as [string, string]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.hero}>
            {me ? (
              <>
                <View style={styles.row}>
                  <Text style={styles.greet}>{greet}, {me.first_name}</Text>
                  <Pressable onPress={() => router.push('/profile')}><SyncBadge sync={sync} dark /></Pressable>
                </View>
                <View style={styles.pills}>
                  <Pill label={`${me.membership.tier_name} member`} tone="dark" icon="ribbon-outline" />
                  <Pill label={`${money2(me.wallet.store_credit_usd)} credit`} tone="dark" icon="wallet-outline" />
                  <Pill label={`${me.wallet.points.toLocaleString('en-US')} pts`} tone="dark" icon="star-outline" />
                </View>
                <View style={styles.cartRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.cartLbl}>YOUR CART</Text>
                    <Text style={styles.cartNum}>{cartCount ? money2(totals?.total ?? 0) : 'Empty'}</Text>
                    <Text style={styles.cartSub}>
                      {cartCount
                        ? `${cartCount} item${cartCount > 1 ? 's' : ''} · saves ${money2(totals?.savings ?? 0)} · earns ${totals?.points ?? 0} pts`
                        : 'Add from your picks below'}
                    </Text>
                  </View>
                  <Button label="Review" icon="bag-handle" small onPress={() => router.push('/(tabs)/cart')} />
                </View>
                {noSignal ? (
                  <View style={styles.signal}>
                    <Ionicons name="cellular-outline" size={13} color={theme.color.onDarkMuted} />
                    <Text style={styles.signalText}>{me.engagement.connectivity_note}. Everything here is on this phone.</Text>
                  </View>
                ) : null}
              </>
            ) : (
              <Text style={styles.greet}>Loading from Couchbase Lite…</Text>
            )}
          </LinearGradient>

          {recs?.insights?.length ? (
            <>
              <SectionTitle title="For you today" action={<Text style={styles.prov}>from last night's model run</Text>} />
              <Card style={{ padding: 4 }}>
                {recs.insights.map((t, i) => (
                  <View key={i} style={[styles.insight, i > 0 && styles.divider]}>
                    <Ionicons name={/^offer/i.test(t) ? 'gift' : /match/i.test(t) ? 'pricetags' : /run out/i.test(t) ? 'hourglass' : /credit/i.test(t) ? 'wallet' : /left of|sold out/i.test(t) ? 'cube' : 'sparkles'} size={17} color={theme.color.red} />
                    <Text style={styles.insightText}>{t}</Text>
                  </View>
                ))}
              </Card>
            </>
          ) : null}

          {price?.offers.length ? (
            <SectionTitle title="Offers for you" action={<Text style={styles.prov}>from your history and {me?.membership.tier_name} tier</Text>} />
          ) : null}
        </View>
        {price?.offers.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.rail}>
            {price.offers.map((o) => <OfferCard key={o.offer_id} o={o} />)}
          </ScrollView>
        ) : null}

        <View style={styles.body}>
          <SectionTitle title="Running low" action={lines.length ? <Pressable onPress={() => router.push('/(tabs)/cart')}><Text style={styles.link}>Smart cart</Text></Pressable> : null} />
        </View>
        {lines.length === 0 ? (
          <View style={styles.body}><Empty icon="checkmark-done-outline" title="You're stocked up" body="Nothing is due to run out. Your smart cart refills after tomorrow's export." /></View>
        ) : (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.rail}>
            {lines.map((l) => <LowCard key={l.sku} l={l} unit={(price && priceOne(price, l.sku, l.qty)?.unit) || l.unit_price} inCart={cart[l.sku] ?? 0} onAdd={() => addToCart(l.sku, l.qty, 'smart_cart')} />)}
          </ScrollView>
        )}

        <View style={styles.body}>
          {recs?.for_you?.length ? (
            <>
              <SectionTitle title="Picked for you" action={<Pressable onPress={() => router.push('/preferences')}><Text style={styles.link}>Tune</Text></Pressable>} />
              {recs.for_you.map((p) => (
                <PickRow key={p.sku} p={p} ctx={price} match={recs.price_matches.find((m) => m.sku === p.sku)?.competitor_price_usd} inCart={!!cart[p.sku]} onAdd={() => addToCart(p.sku, 1, 'pick')} />
              ))}
            </>
          ) : null}

          {windows.length || marked.length || bundles.length ? (
            <SectionTitle
              title="Today's deals"
              action={<Pressable onPress={() => router.push({ pathname: '/(tabs)/shop', params: { category: 'deals' } })}><Text style={styles.link}>All deals</Text></Pressable>}
            />
          ) : null}
          {windows.map((w) => (
            <Card key={w.id} style={styles.window}>
              <Ionicons name={w.peak ? 'trending-up' : 'time'} size={20} color={theme.color.red} />
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={styles.reqTitle}>{w.label} · {w.pct_off}% off until {hourLabel(w.end_hour)}</Text>
                <Text style={styles.reqSub}>{w.reason}</Text>
              </View>
            </Card>
          ))}
        </View>
        {marked.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.rail}>
            {marked.map(({ p, line }) => {
              const r = rules[p.sku];
              return (
                <Pressable key={p.sku} onPress={() => openProduct(p.sku, 'deal')}>
                  <Card style={styles.low}>
                    <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <ProductThumb category={p.category} size={44} />
                      <Pill label={r.overstock ? `${r.overstock.pct_off}% off` : 'Matched'} tone="red" icon={r.overstock ? 'trending-down' : 'pricetags-outline'} />
                    </View>
                    <Text style={styles.lowName} numberOfLines={2}>{shortName(p.name)}</Text>
                    <Text style={styles.price}>{money2(line.unit)}<Text style={styles.was}>  {money2(p.list_price)}</Text></Text>
                    <Text style={styles.meta} numberOfLines={2}>{r.overstock ? r.overstock.reason : `${r.competitor?.competitor} price, matched`}</Text>
                    <Pressable onPress={() => addToCart(p.sku, 1, 'deal')} disabled={!!cart[p.sku]} style={[styles.add, !!cart[p.sku] && styles.added]}>
                      <Ionicons name={cart[p.sku] ? 'checkmark' : 'add'} size={14} color={cart[p.sku] ? theme.color.ok : theme.color.onDark} />
                      <Text style={[styles.addText, !!cart[p.sku] && { color: theme.color.ok }]}>{cart[p.sku] ? `In cart · ${cart[p.sku]}` : 'Add'}</Text>
                    </Pressable>
                  </Card>
                </Pressable>
              );
            })}
          </ScrollView>
        ) : null}
        <View style={styles.body}>
          {price && bundles.map((b) => {
            const done = b.skus.every((s) => cart[s]);
            return (
              <Card key={b.id} style={styles.bundle}>
                <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
                  <Ionicons name="albums" size={18} color={theme.color.red} />
                  <Text style={[styles.reqTitle, { flex: 1 }]}>{b.name} bundle</Text>
                  <Pill label={`${b.pct_off}% off each`} tone="red" />
                  {b.kind === 'dynamic' ? <Pill label="New today" tone="info" icon="flash-outline" /> : null}
                </View>
                <Text style={styles.reqSub}>{b.skus.map((s) => shortName(price.bySku.get(s)!.name)).join(' + ')}</Text>
                <Text style={styles.meta}>{b.reason}</Text>
                <Button
                  small
                  kind={done ? 'ghost' : 'dark'}
                  icon={done ? 'checkmark' : 'add'}
                  label={done ? 'In your cart' : `Add ${b.skus.filter((s) => !cart[s]).length === b.skus.length ? 'the bundle' : 'the rest'} · save ${money2(bundleSaving(price, b))}`}
                  disabled={done}
                  onPress={() => b.skus.filter((s) => !cart[s]).forEach((s) => addToCart(s, 1, 'bundle'))}
                />
              </Card>
            );
          })}

          {recent.length ? (
            <>
              <SectionTitle title="Recent orders" action={<Pressable onPress={() => router.push('/(tabs)/orders')}><Text style={styles.link}>All</Text></Pressable>} />
              <Card style={{ padding: 4 }}>
                {recent.map((q, i) => {
                  const ui = STATUS_UI[q.status] ?? STATUS_UI.pending;
                  return (
                    <View key={q._id} style={[styles.req, i > 0 && styles.divider]}>
                      <Ionicons name={ui.icon} size={20} color={theme.color.red} />
                      <View style={{ flex: 1 }}>
                        <Text style={styles.reqTitle}>{q.title}</Text>
                        <Text style={styles.reqSub} numberOfLines={2}>
                          {q.summary ?? (q._local || q.status === 'pending' ? 'Saved on this phone - sends automatically when you have a connection.' : '')}
                        </Text>
                      </View>
                      <Pill label={q._local ? 'On device' : ui.label} tone={q._local ? 'warn' : ui.tone} />
                    </View>
                  );
                })}
              </Card>
            </>
          ) : null}

          {me ? (
            <Text style={styles.footnote}>
              Your profile, smart cart and picks come from this morning's Databricks export (model {recs?.model_version ?? 'shopsmart-next-basket'}, {me.export_date})
              and live on this phone in Couchbase Lite. No warehouse query runs when you open the app.
            </Text>
          ) : null}
        </View>
      </ScrollView>
    </View>
  );
}

function OfferCard({ o }: { o: Offer }) {
  const go = () =>
    o.kind === 'sku_pct' && o.sku ? openProduct(o.sku, 'offer')
      : o.kind === 'category_pct' && o.category ? router.push({ pathname: '/(tabs)/shop', params: { category: o.category } })
        : router.push('/(tabs)/cart');
  return (
    <Pressable onPress={go}>
      <LinearGradient colors={theme.gradient.hero as unknown as [string, string]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.offer}>
        <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
          <Ionicons name={o.kind === 'bonus_points' ? 'star' : 'gift'} size={15} color={theme.color.onDark} />
          <Text style={styles.offerKind}>{o.driver ? o.driver.toUpperCase() : 'FOR YOU'}</Text>
        </View>
        <Text style={styles.offerTitle} numberOfLines={2}>{o.title}</Text>
        <Text style={styles.offerWhy} numberOfLines={2}>{o.reason}</Text>
        <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
          {o.expires ? <Pill label={`Ends ${fmtDate(o.expires, { month: 'short', day: 'numeric' })}`} tone="dark" /> : null}
          <Pill label={o.kind === 'bonus_points' ? 'Applies at checkout' : 'Already in your prices'} tone="dark" />
        </View>
      </LinearGradient>
    </Pressable>
  );
}

function LowCard({ l, unit, inCart, onAdd }: { l: CartLine; unit: number; inCart: number; onAdd: () => void }) {
  const d = l.days_to_runout;
  const urgent = d != null && d <= 3;
  return (
    <Pressable onPress={() => openProduct(l.sku, 'smart_cart')}>
      <Card style={styles.low}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <ProductThumb category={l.category} size={44} />
          {d != null ? <Pill label={d <= 0 ? 'Out soon' : `~${d}d left`} tone={urgent ? 'red' : 'warn'} /> : null}
        </View>
        <Text style={styles.lowName} numberOfLines={2}>{shortName(l.name)}</Text>
        <Text style={styles.price}>{money2(unit)}{unit < l.list_price ? <Text style={styles.was}>  {money2(l.list_price)}</Text> : null}</Text>
        {l.on_hand_at_fc < l.qty ? <Text style={styles.short}>{l.on_hand_at_fc ? `Only ${l.on_hand_at_fc} left` : 'Backordered'}</Text> : <Text style={styles.meta}>Qty {l.qty} · aisle {l.aisle ?? '—'}</Text>}
        <Pressable onPress={onAdd} disabled={inCart > 0} style={[styles.add, inCart > 0 && styles.added]}>
          <Ionicons name={inCart ? 'checkmark' : 'add'} size={14} color={inCart ? theme.color.ok : theme.color.onDark} />
          <Text style={[styles.addText, inCart > 0 && { color: theme.color.ok }]}>{inCart ? `In cart · ${inCart}` : 'Add'}</Text>
        </Pressable>
      </Card>
    </Pressable>
  );
}

function PickRow({ p, ctx, match, inCart, onAdd }: { p: Pick; ctx: PriceCtx | null; match?: number; inCart: boolean; onAdd: () => void }) {
  const line = ctx ? priceOne(ctx, p.sku) : null;
  const deal = dealOf(line);
  const unit = line?.unit ?? p.unit_price;
  return (
    <Pressable onPress={() => openProduct(p.sku, 'pick')} style={({ pressed }) => [{ marginBottom: 10, opacity: pressed ? 0.92 : 1 }]}>
      <Card style={styles.pick}>
        <ProductThumb category={p.category} size={56} />
        <View style={{ flex: 1, gap: 3 }}>
          <Text style={styles.pickName} numberOfLines={2}>{shortName(p.name)}</Text>
          <Text style={styles.meta} numberOfLines={1}>{p.reason}</Text>
          <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <Text style={styles.price}>{money2(unit)}</Text>
            {unit < p.list_price ? <Text style={styles.was}>{money2(p.list_price)}</Text> : null}
            {deal ? <Pill label={dealText(deal)} tone="red" icon="trending-down" /> : null}
            {match && match < unit ? <Pill label={`Price match ${money2(match)}`} tone="red" icon="pricetags-outline" /> : null}
            {p.limit_per_household ? <Pill label={`Limit ${p.limit_per_household}`} tone="warn" /> : null}
          </View>
        </View>
        <Pressable onPress={onAdd} disabled={inCart} style={[styles.round, inCart && { backgroundColor: theme.color.okSoft }]} accessibilityLabel="Add to cart">
          <Ionicons name={inCart ? 'checkmark' : 'add'} size={20} color={inCart ? theme.color.ok : theme.color.onDark} />
        </Pressable>
      </Card>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  topbar: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 16, paddingBottom: 10, backgroundColor: theme.color.surface, borderBottomWidth: 1, borderBottomColor: theme.color.border },
  avatar: { width: 34, height: 34, borderRadius: 17, backgroundColor: theme.color.ink, alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: theme.color.onDark, fontWeight: '800', fontSize: theme.type.sm },
  body: { paddingHorizontal: 16, maxWidth: 640, width: '100%', alignSelf: 'center' },
  hero: { borderRadius: 22, padding: 18, marginTop: 14, gap: 12 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  greet: { color: theme.color.onDark, fontSize: theme.type.xl, fontWeight: '900', flex: 1 },
  pills: { flexDirection: 'row', gap: 6, flexWrap: 'wrap' },
  cartRow: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: 'rgba(255,255,255,0.08)', borderRadius: 16, padding: 14 },
  cartLbl: { color: theme.color.onDarkMuted, fontSize: theme.type.xs, fontWeight: '800', letterSpacing: 1.4 },
  cartNum: { color: theme.color.onDark, fontSize: 30, fontWeight: '900', lineHeight: 36 },
  cartSub: { color: theme.color.onDarkMuted, fontSize: theme.type.sm },
  signal: { flexDirection: 'row', gap: 6, alignItems: 'center' },
  signalText: { color: theme.color.onDarkMuted, fontSize: theme.type.xs, flex: 1 },
  prov: { color: theme.color.muted, fontSize: theme.type.xs, fontWeight: '600' },
  link: { color: theme.color.red, fontSize: theme.type.sm, fontWeight: '800' },
  insight: { flexDirection: 'row', gap: 10, alignItems: 'flex-start', padding: 12 },
  insightText: { flex: 1, color: theme.color.text, fontSize: theme.type.sm, lineHeight: 19, fontWeight: '600' },
  divider: { borderTopWidth: 1, borderTopColor: theme.color.border },
  rail: { paddingHorizontal: 16, gap: 10, paddingBottom: 6 },
  low: { width: 150, padding: 12, gap: 6 },
  offer: { width: 236, borderRadius: 18, padding: 14, gap: 7 },
  offerKind: { color: theme.color.onDarkMuted, fontSize: theme.type.xs, fontWeight: '800', letterSpacing: 1.2 },
  offerTitle: { color: theme.color.onDark, fontSize: theme.type.lg, fontWeight: '900', lineHeight: 22, minHeight: 44 },
  offerWhy: { color: theme.color.onDarkMuted, fontSize: theme.type.sm, lineHeight: 18, minHeight: 36 },
  window: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12, marginBottom: 10 },
  bundle: { padding: 14, gap: 6, marginTop: 10 },
  lowName: { fontWeight: '800', color: theme.color.ink, fontSize: theme.type.sm, minHeight: 34 },
  price: { fontWeight: '900', color: theme.color.ink, fontSize: theme.type.md },
  was: { color: theme.color.faint, fontWeight: '500', fontSize: theme.type.xs, textDecorationLine: 'line-through' },
  meta: { color: theme.color.muted, fontSize: theme.type.xs },
  short: { color: theme.color.warn, fontSize: theme.type.xs, fontWeight: '700' },
  add: { flexDirection: 'row', gap: 4, alignItems: 'center', justifyContent: 'center', backgroundColor: theme.color.red, borderRadius: 999, paddingVertical: 7, marginTop: 2 },
  added: { backgroundColor: theme.color.okSoft },
  addText: { color: theme.color.onDark, fontWeight: '800', fontSize: theme.type.sm },
  pick: { flexDirection: 'row', gap: 12, alignItems: 'center', padding: 12 },
  pickName: { fontWeight: '800', color: theme.color.ink, fontSize: theme.type.md },
  round: { width: 36, height: 36, borderRadius: 18, backgroundColor: theme.color.red, alignItems: 'center', justifyContent: 'center' },
  req: { flexDirection: 'row', gap: 10, alignItems: 'center', padding: 12 },
  reqTitle: { fontWeight: '800', color: theme.color.text },
  reqSub: { color: theme.color.muted, fontSize: theme.type.sm },
  footnote: { color: theme.color.faint, fontSize: theme.type.xs, textAlign: 'center', marginVertical: 18, lineHeight: 16 },
});
