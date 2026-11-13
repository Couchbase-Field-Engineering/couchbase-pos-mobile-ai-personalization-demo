import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { useData } from '../data/DataContext';
import { theme } from '../theme';
import { Button, Card, Pill, STATUS_UI } from './components';
import { openAction } from './nav';

/**
 * Live status of one offline-first order or shopper action, read from the
 * local database: "On device" -> pushed -> processed by the backend through
 * AOM -> synced back to this card.
 */
export function OrderTracker({ id, onRetry, onDone, retryLabel = 'Back to cart' }: { id: string; onRetry?: () => void; onDone?: () => void; retryLabel?: string }) {
  const { snap, sync } = useData();
  const q = snap.orders.find((r) => r._id === id);
  if (!q) return null;
  const ui = STATUS_UI[q.status] ?? STATUS_UI.pending;
  const waiting = q._local || q.status === 'pending' || q.status === 'processing';
  const steps = [
    { label: 'Saved in Couchbase Lite on this phone', done: true },
    { label: sync.status === 'paused' || sync.status === 'offline' ? 'Waiting for a connection…' : 'Synced to ShopSmart through Sync Gateway', done: !q._local },
    { label: q.governed === false ? 'Processed by the backend (AOM not configured - ungoverned)' : 'Run by the Shopping Assistant agent through Couchbase AOM', done: !waiting },
    { label: q.status === 'awaiting_approval' ? 'Waiting for Customer Care approval' : 'Result synced back to this phone', done: !waiting && q.status !== 'awaiting_approval' },
  ];
  const limited = q.status === 'failed' && q.reason_code === 'purchase_limit';
  return (
    <Card style={{ padding: 16, gap: 12, marginTop: 16 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Text style={styles.title}>{q.title}</Text>
        <Pill label={q._local ? 'On device' : ui.label} tone={q._local ? 'warn' : ui.tone} icon={ui.icon} />
      </View>
      {steps.map((s, i) => (
        <View key={i} style={{ flexDirection: 'row', gap: 10, alignItems: 'center' }}>
          {s.done ? (
            <Ionicons name="checkmark-circle" size={18} color={theme.color.ok} />
          ) : i === steps.findIndex((x) => !x.done) && sync.status !== 'paused' ? (
            <ActivityIndicator size="small" color={theme.color.red} />
          ) : (
            <Ionicons name="ellipse-outline" size={18} color={theme.color.faint} />
          )}
          <Text style={{ color: s.done ? theme.color.text : theme.color.muted, flex: 1 }}>{s.label}</Text>
        </View>
      ))}
      {q.summary ? <Text style={[styles.summary, q.status === 'failed' && { color: theme.color.danger }]}>{q.summary}</Text> : null}
      {q.item?.backordered?.length ? (
        <View style={styles.bo}>
          {q.item.backordered.map((b) => (
            <Text key={b.sku} style={styles.boText}>Backordered: {b.qty} × {b.name.split(',')[0]} · ETA {b.eta}</Text>
          ))}
        </View>
      ) : null}
      {q.governed ? <Text style={styles.tiny}>Governed by Agent Operations Manager{q.trace_id ? ` · trace ${q.trace_id}` : ''}</Text> : null}
      {limited ? (
        <Button
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
      {q.status === 'failed' && onRetry && !limited ? <Button label={retryLabel} icon="refresh" onPress={onRetry} /> : null}
      <Button
        label={waiting ? 'Done - it will finish in the background' : 'Done'}
        kind={waiting || q.status === 'failed' ? 'ghost' : 'dark'}
        onPress={() => (onDone ? onDone() : router.canGoBack() ? router.back() : router.replace('/(tabs)'))}
      />
    </Card>
  );
}

const styles = StyleSheet.create({
  title: { fontWeight: '900', color: theme.color.ink, fontSize: theme.type.lg, flex: 1, marginRight: 8 },
  summary: { color: theme.color.text, fontSize: theme.type.md, lineHeight: 21 },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs },
  bo: { backgroundColor: theme.color.infoSoft, borderRadius: theme.radiusSm, padding: 10, gap: 4 },
  boText: { color: theme.color.info, fontSize: theme.type.sm, fontWeight: '600' },
});
