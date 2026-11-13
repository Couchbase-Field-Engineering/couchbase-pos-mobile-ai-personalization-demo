/**
 * Couchbase Lite on the device (iOS / Android development build).
 *
 * One database per shopper (`pos-<shopper_id>`), scope `retail`, the same
 * collections as the server. A continuous PUSH_AND_PULL replicator talks to Sync Gateway
 * with a session minted by the backend at sign-in (SessionAuthenticator, same
 * pattern as mobile_field_service). The shopper's Sync Gateway user only has
 * the `shopper:<id>`, `store:<home store>` and `catalog` channels, so the phone
 * only ever holds this shopper's own data, their home store and the catalog.
 *
 * Reads go straight to the local database - every screen renders offline.
 * Writes are only ever new `orders` documents, which the replicator pushes
 * whenever there is connectivity (top of the subway stairs, the parking lot, home Wi-Fi).
 */
import { NativeModules } from 'react-native';
import { SG_URL } from '../config';
import type { OrderRequest, SyncState } from '../types';
import {
  buildSnapshot,
  COLLECTIONS,
  Emitter,
  SCOPE,
  type CollectionName,
  type Doc,
  type LocalDb,
  type Snapshot,
  type SyncSession,
} from './localDb';

/* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-require-imports */

export function isCblAvailable(): boolean {
  const m = NativeModules as Record<string, unknown>;
  return Boolean(m.CblReactnative || m.CblReactNative || m.CBLReactNative || m.CblReactNativeEngine);
}

let engineStarted = false;
function cbl(): any {
  const mod = require('cbl-reactnative');
  if (!engineStarted) {
    new mod.CblReactNativeEngine();
    engineStarted = true;
  }
  return mod;
}

function toObject(doc: any): Record<string, unknown> | null {
  if (!doc) return null;
  for (const fn of ['getData', 'toDictionary']) {
    if (typeof doc[fn] === 'function') {
      const d = doc[fn]();
      if (typeof d === 'string') {
        try {
          return JSON.parse(d);
        } catch {
          return null;
        }
      }
      if (d && typeof d === 'object') return d;
    }
  }
  return typeof doc === 'object' ? doc : null;
}

const ACTIVITY: SyncState['status'][] = ['stopped', 'offline', 'connecting', 'idle', 'busy'];

export class CblDb implements LocalDb {
  readonly engine = 'couchbase-lite' as const;
  private db: any = null;
  private cols = new Map<CollectionName, any>();
  private shopperId = '';
  private path: string | null = null;
  private repl: any = null;
  private session: SyncSession | null = null;
  private paused = false;
  private changes = new Emitter<void>();
  private states = new Emitter<SyncState>();
  private state: SyncState = { engine: 'couchbase-lite', status: 'stopped', lastSyncAt: null, pendingPush: 0 };

  async open(shopperId: string) {
    const { Database, DatabaseConfiguration, FileSystem } = cbl();
    this.shopperId = shopperId;
    const config = new DatabaseConfiguration();
    config.setDirectory(await new FileSystem().getDefaultPath());
    this.db = new Database(`pos-${shopperId.toLowerCase()}`, config);
    await this.db.open();
    for (const name of COLLECTIONS) {
      this.cols.set(name, await this.db.createCollection(name, SCOPE));
    }
    try {
      this.path = await this.db.getPath();
    } catch {
      this.path = null;
    }
    // Local writes (an order queued offline) re-render immediately.
    const orders = this.cols.get('orders');
    await orders?.addChangeListener?.(() => this.changes.emit());
  }

  private async all(col: CollectionName): Promise<Doc[]> {
    const rows: any[] = await this.db.createQuery(`SELECT META().id AS _id, * FROM ${SCOPE}.${col}`).execute();
    return (rows ?? []).map((r) => ({ ...(r[col] ?? r), _id: r._id }) as Doc);
  }

  async read(): Promise<Snapshot> {
    const get = async (col: CollectionName, id: string) => toObject(await this.cols.get(col)?.document(id));
    const rows: any[] = await this.db
      .createQuery(`SELECT META().id AS _id, * FROM ${SCOPE}.orders WHERE shopper_id = '${this.shopperId.replace(/'/g, '')}'`)
      .execute();
    const orders = (rows ?? []).map((r) => ({ ...(r.orders ?? r), _id: r._id }) as OrderRequest);
    const pending = await this.pendingIds();
    return buildSnapshot(this.shopperId, get, (col) => this.all(col), orders.map((r) => (pending.has(r._id) ? { ...r, _local: true } : r)));
  }

  async saveRequest(req: OrderRequest) {
    const { MutableDocument } = cbl();
    const { _id, _local, ...body } = req;
    void _local;
    const doc = new MutableDocument(_id, { ...body, channels: [`shopper:${req.shopper_id}`] });
    await this.cols.get('orders').save(doc);
    this.changes.emit();
    await this.refreshPending();
  }

  async startSync(session: SyncSession) {
    this.session = session;
    if (!session.sg) {
      this.setState({ status: 'error', detail: 'Backend has no Sync Gateway configured (SG_ADMIN_URL).' });
      return;
    }
    const { URLEndpoint, ReplicatorConfiguration, CollectionConfig, SessionAuthenticator, Replicator, ReplicatorType } = cbl();
    const url = session.sg.url || SG_URL;
    const config = new ReplicatorConfiguration(new URLEndpoint(url));
    for (const name of COLLECTIONS) {
      // null channels = everything this shopper's SG user can see (shopper:<id> + store:<id> + catalog)
      config.addCollection(this.cols.get(name), new CollectionConfig(null, null));
    }
    config.setAuthenticator(new SessionAuthenticator(session.sg.session_id, session.sg.cookie_name || 'SyncGatewaySession'));
    config.setContinuous(true);
    config.setReplicatorType(ReplicatorType.PUSH_AND_PULL);
    config.setAcceptOnlySelfSignedCerts(false);
    this.repl = await Replicator.create(config);
    await this.repl.addChangeListener((change: any) => {
      const st = change?.status ?? change;
      const level = Number(typeof st?.getActivityLevel === 'function' ? st.getActivityLevel() : st?.activityLevel ?? st?.activity ?? 0);
      const err = typeof st?.getError === 'function' ? st.getError() : st?.error;
      const status = this.paused ? 'paused' : ACTIVITY[level] ?? 'connecting';
      this.setState({
        status: err && level <= 1 ? 'error' : status,
        detail: err ? String(err?.message ?? err) : undefined,
        lastSyncAt: level === 3 ? Date.now() : this.state.lastSyncAt,
        endpoint: url,
      });
      if (level === 3) void this.refreshPending();
    });
    await this.repl.addDocumentChangeListener(() => {
      this.changes.emit();
      void this.refreshPending();
    });
    this.setState({ status: 'connecting', endpoint: url });
    await this.repl.start(false);
  }

  async setPaused(paused: boolean) {
    this.paused = paused;
    if (!this.repl) return;
    if (paused) {
      await this.repl.stop();
      this.setState({ status: 'paused' });
    } else {
      this.setState({ status: 'connecting' });
      await this.repl.start(false);
    }
  }

  onChange(cb: () => void) {
    return this.changes.on(cb);
  }
  onSyncState(cb: (s: SyncState) => void) {
    cb(this.state);
    return this.states.on(cb);
  }
  location() {
    return this.path;
  }

  async close() {
    try {
      await this.repl?.stop();
      await this.repl?.cleanup?.();
    } catch {
      /* already stopped */
    }
    this.repl = null;
    try {
      await this.db?.close();
    } catch {
      /* already closed */
    }
    this.db = null;
    this.cols.clear();
  }

  private async pendingIds(): Promise<Set<string>> {
    if (!this.repl) return new Set();
    try {
      const r = await this.repl.getPendingDocumentIds(this.cols.get('orders'));
      return new Set<string>(r?.pendingDocumentIds ?? []);
    } catch {
      return new Set();
    }
  }

  private async refreshPending() {
    const n = (await this.pendingIds()).size;
    if (n !== this.state.pendingPush) this.setState({ pendingPush: n });
  }

  private setState(p: Partial<SyncState>) {
    this.state = { ...this.state, ...p };
    this.states.emit(this.state);
  }
}
