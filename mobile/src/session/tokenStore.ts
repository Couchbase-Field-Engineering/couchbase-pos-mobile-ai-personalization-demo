import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

/** Session persistence: Keychain/Keystore on device, localStorage in the browser preview. */
const KEY = 'pospos.session.v1';

export async function readSession(): Promise<string | null> {
  try {
    if (Platform.OS === 'web') return globalThis.localStorage?.getItem(KEY) ?? null;
    return await SecureStore.getItemAsync(KEY);
  } catch {
    return null;
  }
}

export async function writeSession(value: string | null): Promise<void> {
  try {
    if (Platform.OS === 'web') {
      if (value) globalThis.localStorage?.setItem(KEY, value);
      else globalThis.localStorage?.removeItem(KEY);
      return;
    }
    if (value) await SecureStore.setItemAsync(KEY, value);
    else await SecureStore.deleteItemAsync(KEY);
  } catch {
    /* session just won't survive a restart */
  }
}
