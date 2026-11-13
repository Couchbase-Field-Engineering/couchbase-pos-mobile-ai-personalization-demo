import { Ionicons } from '@expo/vector-icons';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useData } from '@/src/data/DataContext';
import { dealOf, dealText, priceOne } from '@/src/pricing';
import { theme } from '@/src/theme';
import type { Product } from '@/src/types';
import { ChoiceRow } from '@/src/ui/Choice';
import { Card, Empty, money2, Pill, ProductThumb, shortName, SyncBadge } from '@/src/ui/components';

/** The whole catalog, searchable from Couchbase Lite - prices, live promotions, stock notes and aisles work with no signal. */
export default function Shop() {
  const { snap, sync, cart, addToCart, price } = useData();
  const params = useLocalSearchParams<{ category?: string }>();
  const insets = useSafeAreaInsets();
  const [q, setQ] = useState('');
  const [category, setCategory] = useState<string>('all');
  const cat = snap.catalog;
  const me = snap.shopper;
  // An offer or the Home screen's "All deals" link opens the tab already filtered.
  useEffect(() => {
    if (params.category) setCategory(params.category);
  }, [params.category]);
  const rules = snap.pricing?.sku_rules ?? {};
  const onDeal = (sku: string) => !!price && !!dealOf(priceOne(price, sku));
  const hidden = new Set(me?.preferences.avoid_categories ?? []);
  const buys = me?.replenishment ?? {};

  const items = useMemo(() => {
    const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return (cat?.products ?? [])
      .filter((p) => category === 'all' || (category === 'deals' ? onDeal(p.sku) : p.category === category))
      .filter((p) => {
        if (!words.length) return true;
        const hay = `${p.name} ${p.sku} ${p.tags.join(' ')} ${cat?.categories[p.category] ?? ''}`.toLowerCase();
        return words.every((w) => hay.includes(w));
      })
      // What this shopper buys comes first; categories they hid go last.
      .sort((a, b) => Number(hidden.has(a.category)) - Number(hidden.has(b.category)) || Number(!!buys[b.sku]) - Number(!!buys[a.sku]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, category, cat, me?.rev, price]);

  const options = [{ value: 'all', label: 'All' }, { value: 'deals', label: 'Deals' }, ...Object.entries(cat?.categories ?? {}).map(([value, label]) => ({ value, label }))];

  const Row = ({ p }: { p: Product }) => {
    const line = price ? priceOne(price, p.sku) : null;
    const unit = line?.unit ?? p.list_price;
    const deal = dealOf(line);
    const noPromo = rules[p.sku]?.no_promo;
    const src = deal ? (deal.rule === 'offer' ? 'offer' : 'deal') : 'browse';
    const aisle = snap.store?.aisles?.[p.sku];
    const inCart = cart[p.sku] ?? 0;
    return (
      <Pressable onPress={() => router.push({ pathname: '/product', params: { sku: p.sku, src } })} style={({ pressed }) => [{ marginBottom: 10, opacity: pressed ? 0.92 : 1 }]}>
        <Card style={styles.row}>
          <ProductThumb category={p.category} size={56} />
          <View style={{ flex: 1, gap: 3 }}>
            <Text style={styles.name} numberOfLines={2}>{shortName(p.name)}</Text>
            <Text style={styles.meta} numberOfLines={1}>{p.pack}{aisle ? ` · aisle ${aisle}` : ''}</Text>
            <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <Text style={styles.price}>{money2(unit)}</Text>
              {unit < p.list_price ? <Text style={styles.was}>{money2(p.list_price)}</Text> : null}
              {deal ? <Pill label={dealText(deal)} tone="red" icon={deal.rule === 'offer' ? 'gift-outline' : deal.rule === 'time' ? 'time-outline' : 'trending-down'} /> : null}
              {noPromo ? <Pill label={noPromo.label} tone="warn" icon="flame-outline" /> : null}
              {buys[p.sku] ? <Pill label="You buy this" tone="neutral" icon="repeat-outline" /> : null}
              {p.limit_per_household ? <Pill label={`Limit ${p.limit_per_household}`} tone="warn" /> : null}
            </View>
          </View>
          <Pressable onPress={() => addToCart(p.sku, 1, src)} style={[styles.round, inCart > 0 && { backgroundColor: theme.color.ink }]} accessibilityLabel={`Add ${shortName(p.name)} to cart`}>
            {inCart ? <Text style={styles.count}>{inCart}</Text> : <Ionicons name="add" size={20} color={theme.color.onDark} />}
          </Pressable>
        </Card>
      </Pressable>
    );
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.bg }}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
          <View style={{ flex: 1 }}>
            <Text style={styles.h1}>Shop</Text>
            <Text style={styles.sub}>{cat?.products.length ?? 0} items on this phone{snap.store ? ` · ${snap.store.name}` : ''}</Text>
          </View>
          <SyncBadge sync={sync} />
        </View>
        <View style={styles.search}>
          <Ionicons name="search" size={16} color={theme.color.faint} />
          <TextInput value={q} onChangeText={setQ} placeholder="Coffee pods, diapers, air fryer…" placeholderTextColor={theme.color.faint} style={styles.input} />
          {q ? <Pressable onPress={() => setQ('')}><Ionicons name="close-circle" size={18} color={theme.color.faint} /></Pressable> : null}
        </View>
        <ChoiceRow options={options} value={category} onChange={setCategory} />
      </View>
      <FlatList
        data={items}
        keyExtractor={(p) => p.sku}
        renderItem={({ item }) => <Row p={item} />}
        contentContainerStyle={styles.list}
        keyboardShouldPersistTaps="handled"
        ListEmptyComponent={category === 'deals' && !q
          ? <Empty icon="pricetags-outline" title="No promotions right now" body="Prices here follow stock, the hour and competitor prices. Every price is already your member price." />
          : <Empty icon="search-outline" title="Nothing matches" body="Try a different word or pick another category." />}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  header: { paddingHorizontal: 16, paddingBottom: 8, backgroundColor: theme.color.surface, borderBottomWidth: 1, borderBottomColor: theme.color.border, gap: 10 },
  h1: { fontSize: theme.type.title, fontWeight: '900', color: theme.color.ink },
  sub: { color: theme.color.muted, marginTop: 2, fontSize: theme.type.sm },
  search: { flexDirection: 'row', gap: 8, alignItems: 'center', backgroundColor: theme.color.bg, borderRadius: 999, paddingHorizontal: 14 },
  input: { flex: 1, paddingVertical: 10, fontSize: theme.type.md, color: theme.color.text },
  list: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center' },
  row: { flexDirection: 'row', gap: 12, alignItems: 'center', padding: 12 },
  name: { fontWeight: '800', color: theme.color.ink, fontSize: theme.type.md },
  meta: { color: theme.color.muted, fontSize: theme.type.sm },
  price: { fontWeight: '900', color: theme.color.ink, fontSize: theme.type.md },
  was: { color: theme.color.faint, fontSize: theme.type.xs, textDecorationLine: 'line-through' },
  round: { width: 36, height: 36, borderRadius: 18, backgroundColor: theme.color.red, alignItems: 'center', justifyContent: 'center' },
  count: { color: theme.color.onDark, fontWeight: '900' },
});
