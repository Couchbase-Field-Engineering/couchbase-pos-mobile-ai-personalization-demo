import Constants from 'expo-constants';
import { Platform } from 'react-native';

/**
 * Where the app finds the demo backend and Sync Gateway.
 *
 * iOS simulator / web on the same Mac: localhost works.
 * Android emulator: 10.0.2.2. Physical phone: your Mac's LAN IP.
 */
function hostGuess(): string {
  const dbg = (Constants.expoConfig as { hostUri?: string } | null)?.hostUri ?? '';
  const lan = dbg.split(':')[0];
  if (lan && lan !== 'localhost' && lan !== '127.0.0.1') return lan;
  return Platform.OS === 'android' ? '10.0.2.2' : 'localhost';
}

/**
 * "same-origin" (the web build served by the `web` nginx, in Docker Compose
 * and in Kubernetes): call the backend through that nginx, which proxies
 * /api to it - so the browser only ever needs the one URL it opened.
 */
function apiUrl(): string {
  const v = process.env.EXPO_PUBLIC_API_URL;
  if (v === 'same-origin' && Platform.OS === 'web' && typeof window !== 'undefined') return window.location.origin;
  return v && v !== 'same-origin' ? v : `http://${hostGuess()}:8204`;
}

export const API_URL = apiUrl().replace(/\/$/, '');
export const SG_URL = (process.env.EXPO_PUBLIC_SG_URL || `ws://${hostGuess()}:4992/pos`).replace(/\/$/, '');

/** "auto" = Couchbase Lite when the native module is linked (dev build), browser preview otherwise. */
export const DB_ENGINE = (process.env.EXPO_PUBLIC_DB_ENGINE || 'auto') as 'auto' | 'cbl' | 'preview';
export const PREVIEW_PULL_MS = Number(process.env.EXPO_PUBLIC_PREVIEW_PULL_MS || 4000);
