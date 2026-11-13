import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { useData } from '@/src/data/DataContext';
import { theme } from '@/src/theme';
import { ChoiceRow } from '@/src/ui/Choice';
import { Button, Card, Empty, SectionTitle } from '@/src/ui/components';
import { OrderTracker } from '@/src/ui/OrderTracker';

type Mode = 'more' | 'hide' | 'none';

/**
 * What the shopper wants more of, and what to hide. Saved offline like an
 * order; when it syncs, the backend re-ranks the recommendations at once -
 * no waiting for tomorrow's model run.
 */
export default function Preferences() {
  const { snap, request } = useData();
  const me = snap.shopper;
  const cat = snap.catalog;
  const [modes, setModes] = useState<Record<string, Mode>>(() => {
    const m: Record<string, Mode> = {};
    for (const c of me?.preferences.favorite_categories ?? []) m[c] = 'more';
    for (const c of me?.preferences.avoid_categories ?? []) m[c] = 'hide';
    return m;
  });
  const [option, setOption] = useState<string>(me?.delivery.default_option ?? 'Same-day delivery');
  const [subs, setSubs] = useState<boolean>(me?.preferences.allow_substitutions ?? true);
  const [reqId, setReqId] = useState<string | null>(null);
  if (!me || !cat) return <Empty icon="options-outline" title="Not on this phone yet" body="Your profile syncs from Couchbase in a moment." />;

  const cycle = (code: string) => setModes((m) => ({ ...m, [code]: m[code] === 'more' ? 'hide' : m[code] === 'hide' ? 'none' : 'more' }));
  const pick = (mode: Mode) => Object.entries(modes).filter(([, v]) => v === mode).map(([k]) => k);

  const save = async () => {
    const req = await request(
      'update_preferences',
      { favorite_categories: pick('more'), avoid_categories: pick('hide'), default_delivery_option: option, allow_substitutions: subs },
      'Preferences updated',
    );
    setReqId(req._id);
  };

  return (
    <ScrollView contentContainerStyle={styles.body}>
      {reqId ? <OrderTracker id={reqId} onRetry={() => setReqId(null)} retryLabel="Edit again" /> : (
        <>
          <SectionTitle title="Categories" />
          <Card style={{ padding: 14, gap: 10 }}>
            <Text style={styles.meta}>Tap a category to see more of it, tap again to hide it, and once more to reset.</Text>
            <View style={styles.grid}>
              {Object.entries(cat.categories).map(([code, name]) => {
                const mode = modes[code] ?? 'none';
                return (
                  <Pressable key={code} onPress={() => cycle(code)} style={[styles.chip, mode === 'more' && styles.more, mode === 'hide' && styles.hide]}>
                    <Text style={[styles.chipText, mode === 'more' && { color: theme.color.onDark }, mode === 'hide' && { color: theme.color.faint, textDecorationLine: 'line-through' }]}>
                      {mode === 'more' ? '★ ' : ''}{name}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          </Card>

          <SectionTitle title="Default delivery" />
          <Card style={{ padding: 14 }}>
            <ChoiceRow options={cat.delivery_options.map((w) => ({ value: w, label: w }))} value={option} onChange={setOption} />
          </Card>

          <SectionTitle title="Substitutions" />
          <Card style={styles.row}>
            <View style={{ flex: 1 }}>
              <Text style={{ fontWeight: '800', color: theme.color.text }}>Allow substitutions</Text>
              <Text style={styles.meta}>If an item is out of stock, send a similar one instead of backordering.</Text>
            </View>
            <Switch value={subs} onValueChange={setSubs} trackColor={{ true: theme.color.red, false: theme.color.border }} thumbColor={theme.color.surface} {...({ activeThumbColor: theme.color.surface } as object)} />
          </Card>

          <View style={{ marginTop: 20, gap: 8 }}>
            <Button label="Save preferences" icon="checkmark" onPress={() => void save()} />
            <Text style={styles.tiny}>Saved in Couchbase Lite and sent when you have a connection. Your picks re-rank on the next sync.</Text>
          </View>
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  body: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center', paddingBottom: 40 },
  meta: { color: theme.color.muted, fontSize: theme.type.sm, lineHeight: 19 },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs, textAlign: 'center', lineHeight: 16 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { paddingHorizontal: 14, paddingVertical: 9, borderRadius: 999, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.surface },
  more: { backgroundColor: theme.color.red, borderColor: theme.color.red },
  hide: { backgroundColor: theme.color.bg },
  chipText: { fontWeight: '700', color: theme.color.text },
  row: { flexDirection: 'row', gap: 12, alignItems: 'center', padding: 14 },
});
