import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput } from 'react-native';
import { useData } from '@/src/data/DataContext';
import { theme } from '@/src/theme';
import { Button, Card, Empty, money2, Pill, shortName } from '@/src/ui/components';
import { TOOL_LABEL } from '@/src/ui/nav';
import { OrderTracker } from '@/src/ui/OrderTracker';

const NEEDS_APPROVAL = new Set(['request_price_match', 'issue_goodwill_credit']);
const BLURB: Record<string, string> = {
  request_price_match: 'Customer Care reviews price matches in the Couchbase Labs Agent Operations Manager before the order is placed. You\'ll see their decision here.',
  issue_goodwill_credit: 'Goodwill credits are reviewed in the Couchbase Labs Agent Operations Manager before they land in your store credit.',
  set_subscription: 'The item ships at your member price on the schedule you set, and leaves your smart cart.',
  create_return: 'The refund goes to your store credit right away. Drop the item at any returns desk within 14 days.',
  update_preferences: 'Your recommendations are re-ranked as soon as this syncs.',
  place_order: 'Priced at your member tier; stock is allocated at your fulfillment center when it syncs.',
};
const LABELS: Record<string, string> = {
  sku: 'Item', qty: 'Quantity', cadence_weeks: 'Every (weeks)', order_id: 'Order', competitor: 'Competitor',
  competitor_price_usd: 'Their price', amount_usd: 'Amount', delivery_option: 'Delivery', notes: 'Notes',
};

/** One-tap actions from the Assistant, a product page and the Orders list land here before they're queued. */
export default function Confirm() {
  const { tool = '', args = '{}', title = '' } = useLocalSearchParams<{ tool: string; args: string; title: string }>();
  const { request, snap } = useData();
  const [reqId, setReqId] = useState<string | null>(null);
  const parsed = JSON.parse(args) as Record<string, unknown> & { reason?: string; lines?: { sku: string; qty: number }[] };
  const [reason, setReason] = useState(String(parsed.reason ?? ''));
  const me = snap.shopper;
  if (!me) return <Empty icon="alert-circle-outline" title="Not signed in" body="Sign in to continue." />;

  const products = snap.catalog?.products ?? [];
  const nameOf = (sku: string) => shortName(products.find((p) => p.sku === sku)?.name ?? sku);
  const submit = async () => {
    const req = await request(tool, { ...parsed, ...(reason ? { reason } : {}) }, title || TOOL_LABEL[tool] || tool);
    setReqId(req._id);
  };
  const lines = parsed.lines ?? [];
  const show = (k: string, v: unknown) =>
    k === 'sku' ? nameOf(String(v)) : typeof v === 'number' && k.endsWith('usd') ? money2(v) : String(v);

  return (
    <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
      {reqId ? <OrderTracker id={reqId} onRetry={() => setReqId(null)} retryLabel="Try again" /> : (
        <Card style={{ padding: 18, gap: 12 }}>
          <Pill label={TOOL_LABEL[tool] ?? tool} tone={NEEDS_APPROVAL.has(tool) ? 'red' : 'info'} />
          <Text style={styles.h2}>{title}</Text>
          <Text style={styles.meta}>{me.first_name} {me.last_name} · {me.membership.tier_name} member</Text>
          {lines.length ? (
            <Text style={styles.lines}>{lines.map((l) => `${l.qty} × ${nameOf(l.sku)}`).join('\n')}</Text>
          ) : null}
          {Object.entries(parsed).filter(([k, v]) => !['lines', 'reason'].includes(k) && v !== '' && v != null).map(([k, v]) => (
            <Text key={k} style={styles.meta}>{LABELS[k] ?? k.replace(/_/g, ' ')}: <Text style={{ color: theme.color.text, fontWeight: '700' }}>{show(k, v)}</Text></Text>
          ))}
          {'reason' in parsed || tool === 'issue_goodwill_credit' ? (
            <TextInput value={reason} onChangeText={setReason} placeholder="Reason (goes on the audit trail)" placeholderTextColor={theme.color.faint} style={styles.field} />
          ) : null}
          <Text style={styles.meta}>{BLURB[tool] ?? 'Saved on this phone and sent as soon as you\'re connected.'}</Text>
          <Button label={NEEDS_APPROVAL.has(tool) ? 'Submit for approval' : 'Confirm'} icon="checkmark" onPress={() => void submit()} />
        </Card>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  body: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center' },
  h2: { fontSize: theme.type.xl, fontWeight: '900', color: theme.color.ink },
  meta: { color: theme.color.muted, fontSize: theme.type.sm, lineHeight: 19 },
  lines: { color: theme.color.text, fontSize: theme.type.sm, lineHeight: 20, backgroundColor: theme.color.bg, borderRadius: theme.radiusSm, padding: 10 },
  field: { borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radiusSm, padding: 11, fontSize: theme.type.md, color: theme.color.text },
});
