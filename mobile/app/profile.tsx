import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { API_URL } from '@/src/config';
import { useData } from '@/src/data/DataContext';
import { useAuth } from '@/src/session/AuthContext';
import { theme } from '@/src/theme';
import { Brand, Button, Card, money2, Pill, SectionTitle, shortName, SyncBadge, type IconName } from '@/src/ui/components';

export default function Profile() {
  const { snap, sync, offline, setOffline, dbLocation } = useData();
  const { signOut, session } = useAuth();
  const me = snap.shopper;
  const store = snap.store;
  const contexts = Object.keys(snap.contexts).length;
  const tier = snap.catalog?.member_tiers.find((t) => t.code === me?.membership.tier);

  return (
    <ScrollView style={{ flex: 1, backgroundColor: theme.color.bg }} contentContainerStyle={styles.body}>
      {me ? (
        <Card style={{ padding: 16, gap: 6 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
            <View style={styles.avatar}><Text style={{ fontWeight: '900', color: theme.color.onDark, fontSize: 18 }}>{me.first_name[0]}{me.last_name[0]}</Text></View>
            <View style={{ flex: 1 }}>
              <Text style={styles.name}>{me.first_name} {me.last_name}</Text>
              <Text style={styles.meta}>{me.email} · member #{me.member_number}</Text>
            </View>
          </View>
          <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
            <Pill label={`${me.membership.tier_name} · ${me.membership.discount_pct}% off`} tone="red" icon="ribbon-outline" />
            <Pill label={`${money2(me.wallet.store_credit_usd)} store credit`} tone="neutral" icon="wallet-outline" />
            <Pill label={`${me.wallet.points.toLocaleString('en-US')} points`} tone="neutral" icon="star-outline" />
          </View>
          {tier ? <Text style={[styles.meta, { marginTop: 6 }]}>{tier.blurb}{me.membership.renewal_date ? ` Renews ${me.membership.renewal_date}.` : ''}</Text> : null}
        </Card>
      ) : null}

      <SectionTitle title="Offline & sync" />
      <Card style={{ padding: 16, gap: 10 }}>
        <View style={styles.switchRow}>
          <View style={{ flex: 1 }}>
            <Text style={{ fontWeight: '800', color: theme.color.text, fontSize: theme.type.md }}>No signal</Text>
            <Text style={styles.meta}>Pause replication to demo the subway tunnel: browse, price a cart, check out, ask the assistant - all from Couchbase Lite. Orders queue and send when you switch back.</Text>
          </View>
          <Switch value={offline} onValueChange={setOffline} trackColor={{ true: theme.color.red, false: theme.color.border }} thumbColor={theme.color.surface} {...({ activeThumbColor: theme.color.surface } as object)} />
        </View>
        <SyncBadge sync={sync} />
        <Row icon="server-outline" label="Local database" value={sync.engine === 'couchbase-lite' ? 'Couchbase Lite' : 'Browser preview (in-memory)'} />
        <Row icon="folder-outline" label="Location" value={dbLocation ?? '—'} />
        <Row icon="swap-vertical-outline" label="Replicating with" value={sync.endpoint ?? '—'} />
        <Row icon="time-outline" label="Last sync" value={sync.lastSyncAt ? new Date(sync.lastSyncAt).toLocaleTimeString() : '—'} />
        <Row icon="cloud-upload-outline" label="Waiting to send" value={String(sync.pendingPush)} />
        <Row icon="layers-outline" label="On this phone" value={`your profile · ${snap.recs?.lines.length ?? 0} smart-cart items · ${snap.recs?.for_you.length ?? 0} picks · ${snap.orders.length} orders · ${contexts} cached contexts · catalog ${snap.catalog?.products.length ?? 0} SKUs · ${store ? Object.keys(store.aisles).length : 0} aisle locations`} />
        {sync.detail ? <Text style={[styles.tiny, { color: theme.color.danger }]}>{sync.detail}</Text> : null}
      </Card>

      {me ? (
        <>
          <SectionTitle title="Shopping" action={<Text style={styles.link} onPress={() => router.push('/preferences')}>Edit preferences</Text>} />
          <Card style={{ padding: 16, gap: 10 }}>
            <Row icon="storefront-outline" label="Home store" value={store ? `${store.name} · ${store.street}, ${store.city} · ${store.hours}` : me.home_store?.name ?? '—'} />
            {store ? <Row icon="construct-outline" label="Services" value={store.services.join(', ')} /> : null}
            <Row icon="car-outline" label="Delivery" value={`${me.delivery.default_option} to ${me.address.street}, ${me.address.city}${me.delivery.notes ? ` · ${me.delivery.notes}` : ''}`} />
            <Row icon="card-outline" label="Pays with" value={me.wallet.payment_method} />
            <Row icon="people-outline" label="Household" value={`${me.household.type_name} of ${me.household.size}${me.household.pets?.length ? ` · ${me.household.pets.join(', ')}` : ''}`} />
            <Row icon="repeat-outline" label="Subscribe & Save" value={me.subscriptions.length ? me.subscriptions.map((s) => `${s.qty} × ${shortName(s.name)} every ${s.cadence_weeks} wks (next ${s.next_date})`).join('; ') : 'Nothing on a schedule yet'} />
            <Row icon="cellular-outline" label="Connectivity" value={me.engagement.connectivity_note} />
          </Card>
        </>
      ) : null}

      <SectionTitle title="Where the data comes from" />
      <Card style={{ padding: 16, gap: 8 }}>
        <Row icon="analytics-outline" label="Source" value={`Databricks daily export · ${me?.export_date ?? '—'} · one job a night`} />
        <Row icon="git-branch-outline" label="Model" value={`${snap.recs?.model_version ?? 'shopsmart-next-basket'}${snap.recs?.segment ? ` · segment "${snap.recs.segment}"` : ''}`} />
        <Row icon="cube-outline" label="Served by" value="Couchbase Server → Sync Gateway → Couchbase Lite. No SQL warehouse or Model Serving call per screen." />
        <Text style={styles.tiny}>Your own channel carries your profile, recommendations, orders and assistant history. Your home store's channel carries its aisle map, and the catalog channel is shared by every shopper.</Text>
      </Card>

      <SectionTitle title="Agent Operations Manager" />
      <Card style={{ padding: 16, gap: 8 }}>
        <Text style={styles.meta}>
          Every order, price match, subscription, return and preference change becomes a request that the Shopping Assistant agent runs
          through Couchbase AOM: RBAC and tool scope, human approval for price matches and goodwill credits, a trace and an audit entry.
          Assistant answers use AOM's Knowledge Base, Context Cache, memory and LLM cache.
        </Text>
        <Row icon="link-outline" label="Backend" value={API_URL} />
        <Row icon="finger-print-outline" label="Shopper ID" value={session?.shopper_id ?? '—'} />
      </Card>

      <View style={{ marginVertical: 24, gap: 16, alignItems: 'center' }}>
        <View style={{ alignSelf: 'stretch' }}><Button label="Sign out" kind="ghost" icon="log-out-outline" onPress={() => void signOut()} /></View>
        <Brand size={16} />
        <Text style={styles.tiny}>ShopSmart is a fictional retailer built to demonstrate Couchbase.</Text>
      </View>
    </ScrollView>
  );
}

function Row({ icon, label, value }: { icon: IconName; label: string; value: string }) {
  return (
    <View style={{ flexDirection: 'row', gap: 10, alignItems: 'flex-start' }}>
      <Ionicons name={icon} size={16} color={theme.color.red} style={{ marginTop: 1 }} />
      <Text style={[styles.meta, { width: 104 }]}>{label}</Text>
      <Text style={{ flex: 1, color: theme.color.text, fontWeight: '600', fontSize: theme.type.sm, lineHeight: 19 }} selectable>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { padding: 16, maxWidth: 640, width: '100%', alignSelf: 'center' },
  avatar: { width: 52, height: 52, borderRadius: 26, backgroundColor: theme.color.ink, alignItems: 'center', justifyContent: 'center' },
  name: { fontSize: theme.type.xl, fontWeight: '900', color: theme.color.ink },
  meta: { color: theme.color.muted, fontSize: theme.type.sm, lineHeight: 19 },
  tiny: { color: theme.color.faint, fontSize: theme.type.xs, lineHeight: 16 },
  link: { color: theme.color.red, fontSize: theme.type.sm, fontWeight: '800' },
  switchRow: { flexDirection: 'row', gap: 12, alignItems: 'center', borderBottomWidth: 1, borderBottomColor: theme.color.border, paddingBottom: 12, marginBottom: 2 },
});
