import { Ionicons } from '@expo/vector-icons';
import { Redirect } from 'expo-router';
import { useEffect, useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, type Persona } from '@/src/api';
import { API_URL } from '@/src/config';
import { useAuth } from '@/src/session/AuthContext';
import { theme } from '@/src/theme';
import { Brand, Button, Pill } from '@/src/ui/components';

const FALLBACK: Persona[] = [
  { shopper_id: 'S00101001', name: 'Maya Chen', last_name: 'Chen', member_number: '204817', city: 'Salem, MA', tier: 'Plus', smart_cart_items: 8, home_store: 'ShopSmart Salem', blurb: 'Shops on a subway commute with no signal. A price match on the stand mixer needs approval.' },
  { shopper_id: 'S00101002', name: 'Devon Walker', last_name: 'Walker', member_number: '318552', city: 'Rhinebeck, NY', tier: 'Member', smart_cart_items: 4, home_store: 'ShopSmart Rhinebeck', blurb: 'Rural dead zones, two dogs, and dog food that is backordered.' },
  { shopper_id: 'S00101003', name: 'Sofia Alvarez', last_name: 'Alvarez', member_number: '427190', city: 'Plano, TX', tier: 'Premier', smart_cart_items: 6, home_store: 'ShopSmart Plano', blurb: 'Household of five. Wants a second limited-release console.' },
];

export default function Login() {
  const { session, signIn, notice } = useAuth();
  const insets = useSafeAreaInsets();
  const [member, setMember] = useState('');
  const [last, setLast] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [personas, setPersonas] = useState<Persona[]>(FALLBACK);

  useEffect(() => {
    api<{ personas: Persona[] }>('/api/demo/personas', { timeoutMs: 5000 })
      .then((r) => r.personas.length && setPersonas(r.personas))
      .catch(() => {});
  }, []);

  if (session) return <Redirect href="/(tabs)" />;

  const go = async (m = member, l = last) => {
    setBusy(true);
    setErr(null);
    try {
      await signIn(m, l);
    } catch (ex) {
      setErr((ex as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: theme.color.surface }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={[styles.wrap, { paddingTop: insets.top + 24, paddingBottom: insets.bottom + 24 }]} keyboardShouldPersistTaps="handled">
        <Brand size={24} />
        <Text style={styles.hero}>Shopping that{'\n'}knows you.{'\n'}<Text style={{ color: theme.color.red }}>Even offline.</Text></Text>
        <Text style={styles.sub}>Sign in for your smart cart, picks chosen for you and member prices - all on your phone, with or without signal.</Text>

        <View style={styles.form}>
          {notice ? (
            <View style={styles.notice}>
              <Ionicons name="time-outline" size={16} color={theme.color.ink} />
              <Text style={styles.noticeText}>{notice}</Text>
            </View>
          ) : null}
          <Text style={styles.label}>MEMBER NUMBER</Text>
          <TextInput value={member} onChangeText={setMember} placeholder="e.g. 204817" placeholderTextColor={theme.color.faint} style={styles.input} keyboardType="number-pad" />
          <Text style={styles.label}>LAST NAME</Text>
          <TextInput value={last} onChangeText={setLast} placeholder="e.g. Chen" placeholderTextColor={theme.color.faint} style={styles.input} autoCapitalize="words" />
          {err ? <Text style={styles.err}>{err}</Text> : null}
          <Button label="Sign in" onPress={() => void go()} busy={busy} disabled={!member || !last} icon="log-in-outline" />
        </View>

        <Text style={[styles.label, { marginTop: 26 }]}>DEMO SHOPPERS · FROM TODAY'S DATABRICKS EXPORT</Text>
        {personas.map((p) => (
          <Pressable key={p.shopper_id} onPress={() => void go(p.member_number, p.last_name)} style={({ pressed }) => [styles.persona, pressed && { opacity: 0.8 }]}>
            <View style={styles.avatar}>
              <Text style={{ color: theme.color.onDark, fontWeight: '900' }}>{p.name.split(' ').map((s) => s[0]).join('')}</Text>
            </View>
            <View style={{ flex: 1, gap: 3 }}>
              <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                <Text style={styles.pname}>{p.name}</Text>
                <Pill label={p.tier} tone="red" />
              </View>
              <Text style={styles.pmeta}>{p.city} · {p.smart_cart_items} items in smart cart · #{p.member_number}</Text>
              <Text style={styles.pblurb}>{p.blurb}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={theme.color.faint} />
          </Pressable>
        ))}
        <Text style={styles.foot}>ShopSmart is a fictional retailer built to demonstrate Couchbase.{'\n'}Backend: {API_URL}</Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingHorizontal: 22, gap: 10, maxWidth: 520, width: '100%', alignSelf: 'center' },
  hero: { color: theme.color.ink, fontSize: theme.type.hero, fontWeight: '900', marginTop: 24, lineHeight: 39, letterSpacing: -0.8 },
  sub: { color: theme.color.muted, fontSize: theme.type.md, lineHeight: 21 },
  form: { backgroundColor: theme.color.bg, borderRadius: theme.radius, padding: 18, gap: 8, marginTop: 14 },
  label: { color: theme.color.muted, fontSize: theme.type.xs, fontWeight: '800', letterSpacing: 1.3 },
  input: { borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.surface, borderRadius: theme.radiusSm, padding: 13, fontSize: theme.type.lg, color: theme.color.text, marginBottom: 6 },
  err: { color: theme.color.danger, fontWeight: '600' },
  notice: { flexDirection: 'row', gap: 8, alignItems: 'center', backgroundColor: theme.color.warnSoft, borderRadius: theme.radiusSm, padding: 10, marginBottom: 6 },
  noticeText: { flex: 1, color: theme.color.ink, fontWeight: '600', fontSize: theme.type.sm },
  persona: { flexDirection: 'row', gap: 12, alignItems: 'center', backgroundColor: theme.color.surface, borderRadius: theme.radius, padding: 14, borderWidth: 1, borderColor: theme.color.border },
  avatar: { width: 42, height: 42, borderRadius: 21, backgroundColor: theme.color.ink, alignItems: 'center', justifyContent: 'center' },
  pname: { color: theme.color.ink, fontWeight: '800', fontSize: theme.type.md },
  pmeta: { color: theme.color.muted, fontSize: theme.type.sm },
  pblurb: { color: theme.color.text, fontSize: theme.type.sm, lineHeight: 18 },
  foot: { color: theme.color.faint, fontSize: theme.type.xs, marginTop: 16, textAlign: 'center', lineHeight: 16 },
});
