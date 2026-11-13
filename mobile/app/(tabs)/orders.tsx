import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useData } from '@/src/data/DataContext';
import { daysSince } from '@/src/pricing';
import { theme } from '@/src/theme';
import { Button, Card, Empty, money2, Pill, SectionTitle, STATUS_UI, SyncBadge } from '@/src/ui/components';
import { TOOL_ICON, TOOL_LABEL, openAction } from '@/src/ui/nav';

/** Every order and action from this phone - and every order rung up for this shopper in a store - with its live status from Couchbase Lite. */
export default function Orders() {
  const { snap, sync } = useData();
  const insets = useSafeAreaInsets();
  const queued = snap.orders.filter((o) => o._local || o.status === 'pending' || o.status === 'processing' || o.status === 'on_edge');
  const waiting = snap.orders.filter((o) => o.status === 'awaiting_approval' && !o._local);
  const done = snap.orders.filter((o) => !queued.includes(o) && !waiting.includes(o));
  const shopper = snap.shopper;
  // Orders that arrived in the export, minus the ones placed from this phone (they are above).
  const mine = new Set(snap.orders.map((o) => o.item?.order_id).filter(Boolean));
  const history = (shopper?.order_history ?? []).filter((o) => !mine.has(o.order_id));
  const value = snap.orders.filter((o) => o.status === 'confirmed' || o.status === 'partially_shipped').reduce((s, o) => s + Math.max(0, o.charge_usd ?? 0), 0);

  const Item = ({ id }: { id: string }) => {
    const q = snap.orders.find((o) => o._id === id)!;
    const ui = STATUS_UI[q.status] ?? STATUS_UI.pending;
    const limited = q.status === 'failed' && q.reason_code === 'purchase_limit';
    return (
      <Card style={styles.item}>
        <View style={{ flexDirection: 'row', gap: 10, alignItems: 'center' }}>
          <View style={styles.ico}><Ionicons name={TOOL_ICON[q.tool] ?? 'flash'} size={16} color={theme.color.onDark} /></View>
          <View style={{ flex: 1 }}>
            <Text style={styles.title} numberOfLines={2}>{q.title}</Text>
            <Text style={styles.meta}>{TOOL_LABEL[q.tool] ?? q.tool} · {new Date(q.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</Text>
          </View>
          <Pill label={q._local ? 'On device' : ui.label} tone={q._local ? 'warn' : ui.tone} icon={ui.icon} />
        </View>
        {q.summary ? <Text style={[styles.summary, q.status === 'failed' && { color: theme.color.danger }]}>{q.summary}</Text> : null}
        {q.item?.backordered?.length ? (
          <Text style={[styles.meta, { color: theme.color.info, fontWeight: '700' }]}>
            Backordered: {q.item.backordered.map((b) => `${b.qty} × ${b.name.split(',')[0]} (ETA ${b.eta})`).join('; ')}
          </Text>
        ) : null}
        <Text style={styles.tiny}>
          {q.governed ? 'Governed by Couchbase Labs Agent Operations Manager' : q._local ? 'Queued in Couchbase Lite' : q.status === 'pending' ? 'Synced - waiting for the backend' : 'Processed'}
          {q.trace_id ? ` · trace ${q.trace_id.slice(0, 8)}` : ''}
          {q.charge_usd ? ` · ${money2(q.charge_usd)}` : ''}
        </Text>
        {limited ? (
          <Button
            small
            label="Order the rest without it"
            icon="bag-check-outline"
            onPress={() => {
              const blocked = new Set((q.purchase_limits ?? []).map((p) => p.sku));
              const lines = ((q.args.lines as { sku: string; qty: number }[]) ?? []).filter((l) => !blocked.has(l.sku));
              if (lines.length) openAction({ label: 'Order', tool: 'place_order', args: { ...q.args, lines } }, `Order · ${lines.length} item${lines.length > 1 ? 's' : ''}`);
              else router.push('/(tabs)/shop');
            }}
          />
        ) : null}
      </Card>
    );
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.bg }}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.h1}>Orders</Text>
          <Text style={styles.sub}>{snap.orders.length} from this phone and in store · {money2(value)} placed</Text>
        </View>
        <SyncBadge sync={sync} />
      </View>
    <ScrollView contentContainerStyle={styles.body}>
      {snap.orders.length === 0 ? (
        <Empty icon="receipt-outline" title="Nothing yet" body="Check out from your cart. The order is saved here even with no signal." />
      ) : null}
      {queued.length ? (
        <>
          <SectionTitle title={`On device / syncing · ${queued.length}`} />
          {queued.map((q) => <Item key={q._id} id={q._id} />)}
          {sync.status === 'paused' || sync.status === 'offline' ? (
            <Pressable onPress={() => router.push('/profile')}>
              <Text style={styles.hint}>No connection - these send automatically when the replicator reconnects. Account → turn "No signal" off to watch it happen.</Text>
            </Pressable>
          ) : null}
        </>
      ) : null}
      {waiting.length ? (
        <>
          <SectionTitle title={`Awaiting Customer Care · ${waiting.length}`} />
          {waiting.map((q) => <Item key={q._id} id={q._id} />)}
        </>
      ) : null}
      {done.length ? (
        <>
          <SectionTitle title="Completed" />
          {done.map((q) => <Item key={q._id} id={q._id} />)}
        </>
      ) : null}
      {history.length ? (
        <>
          <SectionTitle title="Earlier orders" action={<Text style={styles.tiny}>from the Databricks export</Text>} />
          {history.slice(0, 6).map((o) => (
            <Card key={o.order_id} style={styles.item}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text style={styles.title}>{o.order_id}</Text>
                <Text style={{ fontWeight: '800', color: theme.color.text }}>{money2(o.total_usd)}</Text>
              </View>
              <Text style={styles.meta}>{o.date} · {o.channel_name ?? o.delivery_option ?? 'Delivery'} · {o.status.replace('_', ' ')}</Text>
              <Text style={styles.meta} numberOfLines={2}>{o.lines.map((l) => `${l.qty} × ${l.name.split(',')[0]}`).join(', ')}</Text>
              {o.lines.length && shopper && (daysSince(o.date, shopper.export_date) ?? 99) <= 30 ? (
                <Button
                  small
                  kind="ghost"
                  label={`Return ${o.lines[0].name.split(',')[0]}`}
                  icon="return-down-back"
                  onPress={() => openAction({ label: 'Return', tool: 'create_return', args: { order_id: o.order_id, sku: o.lines[0].sku, qty: 1, reason: 'No longer needed' } }, `Return · ${o.lines[0].name.split(',')[0]}`)}
                />
              ) : null}
            </Card>
          ))}
        </>
      ) : null}
      <View style={{ height: 30 }} />
    </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, paddingHorizontal: 16, paddingBottom: 12, backgroundColor: theme.color.surface, borderBottomWidth: 1, borderBottomColor: theme.color.border },
  body: { paddingHorizontal: 16, maxWidth: 640, width: '100%', alignSelf: 'center' },
  h1: { fontSize: theme.type.title, fontWeight: '900', color: theme.color.ink },
  sub: { color: theme.color.muted, marginTop: 2 },
  item: { padding: 12, gap: 8, marginBottom: 10 },
  ico: { width: 30, height: 30, borderRadius: 15, backgroundColor: theme.color.red, alignItems: 'center', justifyContent: 'center' },
  title: { fontWeight: '800', color: theme.color.text },
  meta: { color: theme.color.muted, fontSize: theme.type.sm },
  summary: { color: theme.color.text, fontSize: theme.type.sm, lineHeight: 19 },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs },
  hint: { color: theme.color.warn, fontSize: theme.type.sm, fontWeight: '600', marginBottom: 8, lineHeight: 19 },
});
