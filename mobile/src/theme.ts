import { Platform, type ViewStyle } from 'react-native';

/**
 * Couchbase palette, taken from the couchbase.com look: Couchbase red on
 * white, near-black type and dark panels, cool light greys. The red is the
 * one in the Couchbase logo (assets/images/couchbase-logo.png).
 */
const shadowCard: ViewStyle = Platform.select({
  ios: { shadowColor: '#0B0B0C', shadowOpacity: 0.08, shadowRadius: 14, shadowOffset: { width: 0, height: 6 } },
  android: { elevation: 3 },
  default: { boxShadow: '0 6px 18px rgba(11,11,12,0.08)' } as ViewStyle,
}) as ViewStyle;

export const theme = {
  color: {
    red: '#EC1218',
    redDark: '#B80D12',
    redSoft: '#FDE8E9',
    ink: '#0B0B0C',
    inkDeep: '#000000',
    slate: '#2A2A2E',
    bg: '#F5F5F7',
    surface: '#FFFFFF',
    text: '#0B0B0C',
    muted: '#5C5C66',
    faint: '#9A9AA3',
    border: '#E3E3E8',
    ok: '#12805C',
    okSoft: '#E3F6EE',
    warn: '#A85B00',
    warnSoft: '#FFF2DE',
    danger: '#B80D12',
    dangerSoft: '#FDE8E9',
    info: '#1A5FD0',
    infoSoft: '#E6EFFD',
    onDark: '#FFFFFF',
    onDarkMuted: 'rgba(255,255,255,0.72)',
  },
  gradient: {
    hero: ['#0B0B0C', '#2A2A2E'] as const,
    ink: ['#0B0B0C', '#2A2A2E'] as const,
    red: ['#B80D12', '#EC1218'] as const,
    pantry: ['#7A4A12', '#C98A2B'] as const,
    household: ['#0F4C81', '#3B8BD4'] as const,
    personal: ['#0E6B6B', '#2FB5A8'] as const,
    baby: ['#8A3D7C', '#D07BC0'] as const,
    pets: ['#5A4632', '#A9855C'] as const,
    kitchen: ['#26364A', '#5A7CA0'] as const,
    electronics: ['#1B1B3A', '#4B4BA8'] as const,
    toys: ['#B8520D', '#F29A3D'] as const,
    outdoor: ['#12603A', '#3FAE6E'] as const,
    ok: ['#0B5A40', '#12805C'] as const,
  },
  space: { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 },
  type: { xs: 11, sm: 13, md: 15, lg: 17, xl: 20, title: 26, hero: 34 },
  radius: 16,
  radiusSm: 10,
  shadow: { card: shadowCard },
} as const;

export type GradientKey = keyof typeof theme.gradient;
