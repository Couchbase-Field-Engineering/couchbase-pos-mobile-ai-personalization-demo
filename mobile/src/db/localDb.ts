import type { Catalog, ChatDoc, ContextDoc, OrderRequest, PricingDoc, RecsDoc, Shopper, Store, SyncState } from '../types';

/**
 * The app's local database. Two implementations with the same contract:
 *
 * - `couchbase-lite` (cblDb.native.ts): the real thing. An on-device Couchbase
 *   Lite database (scope `retail`, same collections as the server) kept in
 *   sync by a continuous push/pull replicator against Sync Gateway. Everything
 *   renders from here, so the app works with no connectivity at all.
 * - `preview` (previewDb.ts): a browser/Expo Go stand-in for demos without a
 *   device build - an in-memory store that polls the backend's REST
 *   `/api/sync/pull` and pushes orders with `/api/sync/push`. Same document
 *   ids and shapes.
 */
export const SCOPE = 'retail';
export const COLLECTIONS = ['shoppers', 'stores', 'recommendations', 'orders', 'assistant', 'context_cache', 'catalog'] as const;
export type CollectionName = (typeof COLLECTIONS)[number];

export const INTENTS = ['reorder', 'pricing', 'availability', 'delivery', 'returns', 'rewards', 'history', 'subscription', 'preferences', 'deals', 'general'];

export type Snapshot = {
  /** The signed-in shopper's profile (the Databricks row plus live state). */
  shopper: Shopper | null;
  /** Their home store: hours, services and the aisle of every product. */
  store: Store | null;
  /** Smart cart, picks and price matches from the next-basket model. */
  recs: RecsDoc | null;
  orders: OrderRequest[];
  chat: ChatDoc | null;
  catalog: Catalog | null;
  /** Dynamic pricing rules for the shopper's fulfillment center: markdowns, hours, competitor caps, bundles, stock. */
  pricing: PricingDoc | null;
  /** Context cache docs by their id (ctx::<shopper>::<intent> / ctx::<shopper>::guide_<intent>). */
  contexts: Record<string, ContextDoc>;
};

export const EMPTY: Snapshot = { shopper: null, store: null, recs: null, orders: [], chat: null, catalog: null, pricing: null, contexts: {} };

export type SyncSession = {
  token: string;
  shopperId: string;
  sg: { session_id: string; cookie_name: string; url: string | null; db: string } | null;
};

export interface LocalDb {
  readonly engine: SyncState['engine'];
  open(shopperId: string): Promise<void>;
  read(): Promise<Snapshot>;
  saveRequest(req: OrderRequest): Promise<void>;
  startSync(session: SyncSession): Promise<void>;
  setPaused(paused: boolean): Promise<void>;
  onChange(cb: () => void): () => void;
  onSyncState(cb: (s: SyncState) => void): () => void;
  location(): string | null;
  close(): Promise<void>;
}

export const ids = {
  shopper: (s: string) => `shopper::${s}`,
  store: (s: string) => `store::${s}`,
  recs: (s: string) => `recs::${s}`,
  chat: (s: string) => `chat::${s}`,
  ctx: (shopperId: string, k: string) => `ctx::${shopperId}::${k}`,
  catalog: 'catalog::v1',
  pricing: (fc: string) => `pricing::${fc}`,
};

export type Doc = Record<string, unknown> & { _id?: string };

/** Build a Snapshot from a by-id getter and a per-collection lister. */
export async function buildSnapshot(
  shopperId: string,
  get: (col: CollectionName, id: string) => Promise<Doc | null>,
  list: (col: CollectionName) => Promise<Doc[]>,
  orders: OrderRequest[],
): Promise<Snapshot> {
  const contexts: Record<string, ContextDoc> = {};
  for (const d of await list('context_cache')) {
    const c = d as unknown as ContextDoc & { _id?: string };
    const key = c._id ?? ids.ctx(shopperId, c.personal ? c.intent : `guide_${c.intent}`);
    contexts[key] = c;
  }
  const shopper = (await get('shoppers', ids.shopper(shopperId))) as unknown as Shopper | null;
  // Only the home store's channel replicates, so the collection holds one document.
  const store = shopper?.home_store
    ? ((await get('stores', ids.store(shopper.home_store.store_id))) as unknown as Store | null)
    : (((await list('stores'))[0] ?? null) as unknown as Store | null);
  return {
    shopper,
    store,
    recs: (await get('recommendations', ids.recs(shopperId))) as unknown as RecsDoc | null,
    chat: (await get('assistant', ids.chat(shopperId))) as unknown as ChatDoc | null,
    catalog: (await get('catalog', ids.catalog)) as unknown as Catalog | null,
    // Only this shopper's fulfillment-center channel replicates, so there is one rules document to read.
    pricing: shopper ? ((await get('catalog', ids.pricing(shopper.delivery.fulfillment_center))) as unknown as PricingDoc | null) : null,
    contexts,
    orders: [...orders].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
  };
}

type Listener<T> = (v: T) => void;
export class Emitter<T> {
  private ls = new Set<Listener<T>>();
  on(l: Listener<T>) {
    this.ls.add(l);
    return () => {
      this.ls.delete(l);
    };
  }
  emit(v: T) {
    this.ls.forEach((l) => {
      try {
        l(v);
      } catch {
        /* listener errors never break sync */
      }
    });
  }
}
