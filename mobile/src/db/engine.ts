import { DB_ENGINE } from '../config';
import { CblDb, isCblAvailable } from './cblDb';
import type { LocalDb } from './localDb';
import { PreviewDb } from './previewDb';

/** Couchbase Lite whenever the native module is linked; the REST preview otherwise. */
export function createLocalDb(): LocalDb {
  if (DB_ENGINE === 'preview') return new PreviewDb();
  if (DB_ENGINE === 'cbl' || isCblAvailable()) return new (CblDb as unknown as new () => LocalDb)();
  return new PreviewDb();
}
