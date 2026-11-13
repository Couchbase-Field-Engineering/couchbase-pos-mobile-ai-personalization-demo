/**
 * Web / non-native stand-in. Metro picks `cblDb.native.ts` on iOS and
 * Android; the browser preview build gets this file, which never loads the
 * Couchbase Lite native module.
 */
import type { LocalDb } from './localDb';

export function isCblAvailable(): boolean {
  return false;
}

export class CblDb {
  constructor() {
    throw new Error('Couchbase Lite needs an iOS or Android development build (npx expo run:ios).');
  }
}

export type { LocalDb };
