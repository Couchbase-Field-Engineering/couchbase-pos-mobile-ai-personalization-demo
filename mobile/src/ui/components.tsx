import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import React from 'react';
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { theme, type GradientKey } from '../theme';
import type { RequestStatus, SyncState } from '../types';

export type IconName = React.ComponentProps<typeof Ionicons>['name'];

const LOGO = require('../../assets/images/couchbase-logo.png');
const LOGO_WHITE = require('../../assets/images/couchbase-logo-white.png');
const LOGO_RATIO = 1024 / 192;

/** The Couchbase logo with the ShopSmart product name beside it. `light` = on a dark background. */
export function Brand({ light = false, size = 20, tag = true }: { light?: boolean; size?: number; tag?: boolean }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: size * 0.45 }}>
      <Image source={light ? LOGO_WHITE : LOGO} style={{ height: size, width: size * LOGO_RATIO }} resizeMode="contain" accessibilityLabel="Couchbase" />
      {tag ? (
        <>
          <View style={{ width: 1, height: size * 0.9, backgroundColor: light ? 'rgba(255,255,255,0.35)' : theme.color.border }} />
          <Text style={{ color: light ? theme.color.onDark : theme.color.ink, fontSize: size * 0.8, fontWeight: '800', letterSpacing: -0.3 }}>
            ShopSmart
          </Text>
        </>
      ) : null}
    </View>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function GradientArt({ palette, icon, height = 96, children, radius = theme.radius, round = 'top' }: {
  palette: string; icon: IconName; height?: number; children?: React.ReactNode; radius?: number; round?: 'top' | 'all';
}) {
  const colors = (theme.gradient as Record<string, readonly [string, string]>)[palette] ?? theme.gradient.hero;
  return (
    <LinearGradient colors={colors as unknown as [string, string]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ height, borderTopLeftRadius: radius, borderTopRightRadius: radius, borderBottomLeftRadius: round === 'all' ? radius : 0, borderBottomRightRadius: round === 'all' ? radius : 0, padding: 14, justifyContent: 'space-between', overflow: 'hidden' }}>
      <View style={styles.waves} pointerEvents="none">
        <Ionicons name={icon} size={height * 0.95} color="rgba(255,255,255,0.13)" />
      </View>
      {children}
    </LinearGradient>
  );
}

export type Tone = 'neutral' | 'red' | 'ok' | 'warn' | 'danger' | 'info' | 'dark';

export function Pill({ label, tone = 'neutral', icon }: { label: string; tone?: Tone; icon?: IconName }) {
  const map = {
    neutral: [theme.color.bg, theme.color.muted],
    red: [theme.color.redSoft, theme.color.redDark],
    ok: [theme.color.okSoft, theme.color.ok],
    warn: [theme.color.warnSoft, theme.color.warn],
    danger: [theme.color.dangerSoft, theme.color.danger],
    info: [theme.color.infoSoft, theme.color.info],
    dark: ['rgba(255,255,255,0.18)', theme.color.onDark],
  } as const;
  const [bg, fg] = map[tone];
  return (
    <View style={[styles.pill, { backgroundColor: bg }]}>
      {icon ? <Ionicons name={icon} size={11} color={fg} /> : null}
      <Text style={{ color: fg, fontSize: theme.type.xs, fontWeight: '700' }}>{label}</Text>
    </View>
  );
}

export function Button({ label, onPress, kind = 'red', icon, disabled, busy, small }: {
  label: string; onPress: () => void; kind?: 'red' | 'dark' | 'ghost' | 'light'; icon?: IconName; disabled?: boolean; busy?: boolean; small?: boolean;
}) {
  const bg = { red: theme.color.red, dark: theme.color.ink, ghost: 'transparent', light: theme.color.surface }[kind];
  const fg = { red: theme.color.onDark, dark: theme.color.onDark, ghost: theme.color.red, light: theme.color.red }[kind];
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || busy}
      style={({ pressed }) => [
        styles.btn,
        small && styles.btnSmall,
        { backgroundColor: bg, opacity: disabled ? 0.45 : pressed ? 0.85 : 1 },
        kind === 'ghost' && { borderWidth: 1, borderColor: theme.color.border },
      ]}
    >
      {busy ? <ActivityIndicator color={fg} /> : icon ? <Ionicons name={icon} size={small ? 14 : 17} color={fg} /> : null}
      <Text style={{ color: fg, fontWeight: '800', fontSize: small ? theme.type.sm : theme.type.md }}>{label}</Text>
    </Pressable>
  );
}

export function SectionTitle({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionText}>{title.toUpperCase()}</Text>
      {action}
    </View>
  );
}

const SYNC_LABEL: Record<SyncState['status'], [string, 'ok' | 'warn' | 'danger' | 'neutral' | 'info']> = {
  connecting: ['Connecting', 'info'],
  connected: ['Synced', 'ok'],
  idle: ['Synced', 'ok'],
  busy: ['Syncing', 'info'],
  offline: ['Offline', 'warn'],
  paused: ['No signal', 'warn'],
  error: ['Sync error', 'danger'],
  stopped: ['Local only', 'neutral'],
};

export function SyncBadge({ sync, dark }: { sync: SyncState; dark?: boolean }) {
  const [label, tone] = SYNC_LABEL[sync.status];
  const text = sync.pendingPush ? `${label} · ${sync.pendingPush} to send` : label;
  const dot = { ok: '#3DDC84', warn: '#FFB454', danger: '#FF6B6B', neutral: theme.color.faint, info: '#5CC8FF' }[tone];
  return (
    <View style={[styles.sync, dark ? { backgroundColor: 'rgba(255,255,255,0.12)' } : { backgroundColor: theme.color.bg }]}>
      <View style={[styles.dot, { backgroundColor: dot }]} />
      <Text style={{ color: dark ? theme.color.onDark : theme.color.text, fontSize: theme.type.xs, fontWeight: '700' }}>
        {sync.engine === 'couchbase-lite' ? 'Couchbase Lite · ' : 'Preview · '}
        {text}
      </Text>
    </View>
  );
}

export const STATUS_UI: Record<RequestStatus, { label: string; tone: Tone; icon: IconName }> = {
  pending: { label: 'Waiting to sync', tone: 'warn', icon: 'cloud-upload-outline' },
  processing: { label: 'Processing', tone: 'info', icon: 'sync' },
  awaiting_approval: { label: 'Awaiting Customer Care', tone: 'red', icon: 'shield-checkmark-outline' },
  confirmed: { label: 'Confirmed', tone: 'ok', icon: 'checkmark-circle' },
  partially_shipped: { label: 'Partially shipped', tone: 'info', icon: 'cube-outline' },
  failed: { label: 'Not completed', tone: 'danger', icon: 'alert-circle' },
  on_edge: { label: 'Held at the store', tone: 'warn', icon: 'storefront-outline' },
};

const CATEGORY_ICON: Record<string, IconName> = {
  pantry: 'basket', household: 'home', personal: 'water', baby: 'happy', pets: 'paw', kitchen: 'restaurant',
  electronics: 'headset', toys: 'game-controller', outdoor: 'bicycle',
};
export const categoryIcon = (category: string): IconName => CATEGORY_ICON[category] ?? 'pricetag';

/** Square product tile in the category's colour - the demo ships no product photography. */
export function ProductThumb({ category, size = 52 }: { category: string; size?: number }) {
  const colors = (theme.gradient as Record<string, readonly [string, string]>)[category] ?? theme.gradient.ink;
  return (
    <LinearGradient colors={colors as unknown as [string, string]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ width: size, height: size, borderRadius: size * 0.24, alignItems: 'center', justifyContent: 'center' }}>
      <Ionicons name={categoryIcon(category)} size={size * 0.48} color="rgba(255,255,255,0.95)" />
    </LinearGradient>
  );
}

/** Product name without the pack-size tail. */
export const shortName = (name: string) => name.split(',')[0];

export const money2 = (n: number | undefined | null) =>
  n == null ? '' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function Empty({ icon, title, body }: { icon: IconName; title: string; body: string }) {
  return (
    <View style={{ alignItems: 'center', padding: theme.space.xl, gap: 6 }}>
      <Ionicons name={icon} size={34} color={theme.color.faint} />
      <Text style={{ fontWeight: '800', color: theme.color.text, fontSize: theme.type.md }}>{title}</Text>
      <Text style={{ color: theme.color.muted, textAlign: 'center' }}>{body}</Text>
    </View>
  );
}

export const money = (n: number | undefined | null) =>
  n == null ? '' : `$${Number(n).toLocaleString('en-US', { maximumFractionDigits: n % 1 ? 2 : 0 })}`;

export function fmtDate(iso: string, opts: Intl.DateTimeFormatOptions = { weekday: 'short', month: 'short', day: 'numeric' }) {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', opts);
}

const styles = StyleSheet.create({
  card: { backgroundColor: theme.color.surface, borderRadius: theme.radius, ...theme.shadow.card },
  waves: { position: 'absolute', right: -6, bottom: -14 },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, alignSelf: 'flex-start' },
  btn: { flexDirection: 'row', gap: 8, alignItems: 'center', justifyContent: 'center', paddingVertical: 14, paddingHorizontal: 18, borderRadius: 999 },
  btnSmall: { paddingVertical: 8, paddingHorizontal: 14 },
  section: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: theme.space.xl, marginBottom: theme.space.sm },
  sectionText: { color: theme.color.muted, fontSize: theme.type.xs, fontWeight: '800', letterSpacing: 1.4 },
  sync: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999, alignSelf: 'flex-start', flexShrink: 0 },
  dot: { width: 7, height: 7, borderRadius: 4 },
});
