import { api, ApiError } from '../api';
import { PREVIEW_PULL_MS } from '../config';
import type { OrderRequest, SyncState } from '../types';
import { buildSnapshot, Emitter, type CollectionName, type Doc, type LocalDb, type Snapshot, type SyncSession } from './localDb';

type Row = { collection: CollectionName; id: string; doc: Record<string, unknown> };

/**
 * Browser / Expo Go preview of the Couchbase Lite flow. Same documents, same
 * offline-first order queue - but held in memory and synced over REST.
 */
export class PreviewDb implements LocalDb {
  readonly engine = 'preview' as const;
  private shopperId = '';
  private docs = new Map<string, Row>();
  private local = new Map<string, OrderRequest>(); // orders not yet acknowledged by the server
  private session: SyncSession | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private paused = false;
  private changes = new Emitter<void>();
  private states = new Emitter<SyncState>();
  private state: SyncState = { engine: 'preview', status: 'stopped', lastSyncAt: null, pendingPush: 0 };

  async open(shopperId: string) {
    this.shopperId = shopperId;
  }

  async read(): Promise<Snapshot> {
    const get = async (col: CollectionName, id: string) => this.docs.get(`${col}/${id}`)?.doc ?? null;
    const list = async (col: CollectionName): Promise<Doc[]> =>
      [...this.docs.values()].filter((r) => r.collection === col).map((r) => ({ ...r.doc, _id: r.id }));
    const server = [...this.docs.values()]
      .filter((r) => r.collection === 'orders')
      .map((r) => ({ ...(r.doc as unknown as OrderRequest), _id: r.id }));
    const serverIds = new Set(server.map((r) => r._id));
    const local = [...this.local.values()].filter((r) => !serverIds.has(r._id));
    return buildSnapshot(this.shopperId, get, list, [...server, ...local]);
  }

  async saveRequest(req: OrderRequest) {
    this.local.set(req._id, { ...req, _local: true });
    this.setState({ pendingPush: this.unpushed().length });
    this.changes.emit();
    void this.tick();
  }

  async startSync(session: SyncSession) {
    this.session = session;
    this.setState({ status: 'connecting', endpoint: 'REST /api/sync (browser preview)' });
    await this.tick();
    if (this.timer) clearInterval(this.timer);
    this.timer = setInterval(() => void this.tick(), PREVIEW_PULL_MS);
  }

  async setPaused(paused: boolean) {
    this.paused = paused;
    this.setState({ status: paused ? 'paused' : 'connecting' });
    if (!paused) await this.tick();
  }

  onChange(cb: () => void) {
    return this.changes.on(cb);
  }
  onSyncState(cb: (s: SyncState) => void) {
    cb(this.state);
    return this.states.on(cb);
  }
  location() {
    return 'in-memory (browser preview)';
  }
  async close() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.docs.clear();
    this.local.clear();
  }

  private unpushed() {
    return [...this.local.values()].filter((r) => r._local);
  }

  private setState(p: Partial<SyncState>) {
    this.state = { ...this.state, ...p };
    this.states.emit(this.state);
  }

  private busy = false;
  private async tick() {
    if (!this.session || this.paused || this.busy) return;
    this.busy = true;
    try {
      const out = this.unpushed();
      if (out.length) {
        const res = await api<{ accepted: string[] }>('/api/sync/push', {
          token: this.session.token,
          body: { docs: out.map(({ _id, _local, ...doc }) => ({ collection: 'orders', id: _id, doc })) },
        });
        for (const r of out) this.local.set(r._id, { ...r, _local: false });
        void res;
        await new Promise((r) => setTimeout(r, 600)); // let the worker pick it up before we pull
      }
      const data = await api<{ docs: Row[] }>('/api/sync/pull', { token: this.session.token });
      const before = JSON.stringify([...this.docs.values()].map((r) => r.doc));
      this.docs.clear();
      for (const r of data.docs) this.docs.set(`${r.collection}/${r.id}`, r);
      for (const id of [...this.local.keys()]) if (this.docs.has(`orders/${id}`)) this.local.delete(id);
      this.setState({ status: 'idle', lastSyncAt: Date.now(), pendingPush: this.unpushed().length, detail: undefined });
      if (JSON.stringify(data.docs.map((r) => r.doc)) !== before || out.length) this.changes.emit();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        // The backend rejected the session: stop polling; the app signs the shopper out.
        if (this.timer) clearInterval(this.timer);
        this.timer = null;
        this.setState({ status: 'error', detail: 'session-expired', pendingPush: this.unpushed().length });
        return;
      }
      this.setState({ status: 'offline', detail: (e as Error).message, pendingPush: this.unpushed().length });
    } finally {
      this.busy = false;
    }
  }
}
