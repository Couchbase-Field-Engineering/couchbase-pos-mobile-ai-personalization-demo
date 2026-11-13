"""Operational store: Couchbase Server (bucket `pos`, scope `retail`) or
an in-process memory store with the same interface for dependency-free runs.

Collections
-----------
Replicated to the phone's Couchbase Lite database through Sync Gateway
(every document carries `channels`):

  shoppers          one per shopper - the Databricks row, plus `rev`
  stores            one per store - hours, services, the aisle of every product
  recommendations   one per shopper - smart cart, "for you" picks, price matches
  orders            orders and other shopper actions; CREATED ON THE PHONE (offline-first), processed here
  assistant         one per shopper - assistant conversation + cached answers
  context_cache     per shopper + intent - the assembled RAG context (Context Caching)
  catalog           products, member tiers, delivery options, FAQ

Server-side only:

  inventory         on-hand units per fulfillment center (CAS-protected)
  lookups           login index, load state, KB document index
"""
from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from datetime import timedelta

from . import config

log = logging.getLogger("pos.store")

SYNCED_COLLECTIONS = ["shoppers", "stores", "recommendations", "orders", "assistant", "context_cache", "catalog"]
SERVER_COLLECTIONS = ["inventory", "lookups"]
ALL_COLLECTIONS = SYNCED_COLLECTIONS + SERVER_COLLECTIONS


class MemoryStore:
    driver = "memory"

    def __init__(self):
        self.data: dict[str, dict[str, dict]] = {c: {} for c in ALL_COLLECTIONS}
        self.expiry: dict[tuple[str, str], float] = {}
        self.cas: dict[tuple[str, str], int] = {}
        self.lock = threading.RLock()
        self.connected = True

    async def connect(self):
        return None

    def _alive(self, col, key):
        exp = self.expiry.get((col, key))
        if exp and exp < time.time():
            self.data[col].pop(key, None)
            self.expiry.pop((col, key), None)
            return False
        return key in self.data[col]

    async def get(self, col, key):
        with self.lock:
            return copy.deepcopy(self.data[col][key]) if self._alive(col, key) else None

    async def get_with_cas(self, col, key):
        with self.lock:
            if not self._alive(col, key):
                return None, None
            return copy.deepcopy(self.data[col][key]), self.cas.get((col, key), 1)

    async def replace_cas(self, col, key, doc, cas) -> bool:
        with self.lock:
            if self.cas.get((col, key), 1) != cas:
                return False
            self.data[col][key] = copy.deepcopy(doc)
            self.cas[(col, key)] = cas + 1
            return True

    async def insert(self, col, key, doc) -> bool:
        with self.lock:
            if self._alive(col, key):
                return False
            await self.upsert(col, key, doc)
            return True

    async def upsert(self, col, key, doc, ttl: int = 0):
        with self.lock:
            self.data[col][key] = copy.deepcopy(doc)
            self.cas[(col, key)] = self.cas.get((col, key), 0) + 1
            if ttl:
                self.expiry[(col, key)] = time.time() + ttl
            else:
                self.expiry.pop((col, key), None)

    async def upsert_many(self, col, docs: dict):
        for k, v in docs.items():
            await self.upsert(col, k, v)

    async def remove(self, col, key):
        with self.lock:
            self.data[col].pop(key, None)
            self.expiry.pop((col, key), None)

    async def keys_with_prefix(self, col, prefix):
        with self.lock:
            return [k for k in list(self.data[col]) if k.startswith(prefix) and self._alive(col, k)]

    async def pending_orders(self, limit=25):
        with self.lock:
            rows = [(k, copy.deepcopy(v)) for k, v in self.data["orders"].items()
                    if v.get("status") in ("pending", "awaiting_approval")]
        rows.sort(key=lambda kv: kv[1].get("created_at", ""))
        return rows[:limit]

    async def orders_for_shopper(self, shopper_id):
        with self.lock:
            return [copy.deepcopy(v) | {"_id": k} for k, v in self.data["orders"].items() if v.get("shopper_id") == shopper_id]

    async def recently_active(self, since: str, limit: int):
        """Shopper ids whose last app open is on or after `since` (ISO date), most recent first."""
        with self.lock:
            rows = sorted(((v.get("engagement", {}).get("last_app_open") or "", v["shopper_id"])
                           for v in self.data["shoppers"].values()), reverse=True)
        return [sid for d, sid in rows if d >= since][:limit]

    async def count(self, col):
        with self.lock:
            return len(self.data[col])


class CouchbaseStore:
    driver = "couchbase"

    def __init__(self):
        self.cluster = None
        self.cols = {}
        self.connected = False

    async def connect(self, retries: int = 60):
        from couchbase.auth import PasswordAuthenticator
        from couchbase.cluster import Cluster
        from couchbase.options import ClusterOptions

        last = None
        for attempt in range(retries):
            try:
                opts = ClusterOptions(PasswordAuthenticator(config.COUCHBASE_USERNAME, config.COUCHBASE_PASSWORD))
                opts.apply_profile("wan_development")
                cluster = await asyncio.to_thread(Cluster, config.COUCHBASE_CONN, opts)
                await asyncio.to_thread(cluster.wait_until_ready, timedelta(seconds=20))
                bucket = cluster.bucket(config.COUCHBASE_BUCKET)
                scope = bucket.scope(config.COUCHBASE_SCOPE)
                self.cols = {c: scope.collection(c) for c in ALL_COLLECTIONS}
                self.cluster = cluster
                # probe one collection so a missing scope fails here, not mid-request
                await self.get("lookups", "probe")
                self.connected = True
                log.info("Connected to Couchbase %s/%s.%s", config.COUCHBASE_CONN, config.COUCHBASE_BUCKET, config.COUCHBASE_SCOPE)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("Couchbase not ready (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(5)
        raise RuntimeError(f"Could not connect to Couchbase: {last}")

    async def get(self, col, key):
        from couchbase.exceptions import DocumentNotFoundException
        try:
            res = await asyncio.to_thread(self.cols[col].get, key)
            return res.content_as[dict]
        except DocumentNotFoundException:
            return None

    async def get_with_cas(self, col, key):
        from couchbase.exceptions import DocumentNotFoundException
        try:
            res = await asyncio.to_thread(self.cols[col].get, key)
            return res.content_as[dict], res.cas
        except DocumentNotFoundException:
            return None, None

    async def replace_cas(self, col, key, doc, cas) -> bool:
        from couchbase.exceptions import CasMismatchException
        from couchbase.options import ReplaceOptions
        try:
            await asyncio.to_thread(self.cols[col].replace, key, doc, ReplaceOptions(cas=cas))
            return True
        except CasMismatchException:
            return False

    async def insert(self, col, key, doc) -> bool:
        from couchbase.exceptions import DocumentExistsException
        try:
            await asyncio.to_thread(self.cols[col].insert, key, doc)
            return True
        except DocumentExistsException:
            return False

    async def upsert(self, col, key, doc, ttl: int = 0):
        from couchbase.options import UpsertOptions
        opts = UpsertOptions(expiry=timedelta(seconds=ttl)) if ttl else None
        if opts:
            await asyncio.to_thread(self.cols[col].upsert, key, doc, opts)
        else:
            await asyncio.to_thread(self.cols[col].upsert, key, doc)

    async def upsert_many(self, col, docs: dict):
        if not docs:
            return
        res = await asyncio.to_thread(self.cols[col].upsert_multi, docs)
        if not res.all_ok:
            failed = list(res.exceptions.keys())[:5]
            raise RuntimeError(f"upsert_multi failed for {len(res.exceptions)} docs, e.g. {failed}")

    async def remove(self, col, key):
        from couchbase.exceptions import DocumentNotFoundException
        try:
            await asyncio.to_thread(self.cols[col].remove, key)
        except DocumentNotFoundException:
            pass

    async def _query(self, statement, **params):
        from couchbase.options import QueryOptions
        def run():
            result = self.cluster.query(statement, QueryOptions(named_parameters=params, adhoc=False))
            return [row for row in result.rows()]
        return await asyncio.to_thread(run)

    def _ks(self, col):
        return f"`{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{col}`"

    async def keys_with_prefix(self, col, prefix):
        rows = await self._query(f"SELECT RAW META(d).id FROM {self._ks(col)} d WHERE META(d).id LIKE $p",
                                 p=prefix.replace("%", "") + "%")
        return rows

    async def pending_orders(self, limit=25):
        rows = await self._query(
            f"SELECT META(o).id AS _id, o.* FROM {self._ks('orders')} o "
            f"WHERE o.type = 'request' AND o.status IN ['pending', 'awaiting_approval'] "
            f"ORDER BY o.created_at LIMIT {int(limit)}")
        return [(row.pop("_id"), row) for row in rows]

    async def orders_for_shopper(self, shopper_id):
        return await self._query(
            f"SELECT META(o).id AS _id, o.* FROM {self._ks('orders')} o "
            f"WHERE o.type = 'request' AND o.shopper_id = $s ORDER BY o.created_at DESC LIMIT 200", s=shopper_id)

    async def recently_active(self, since: str, limit: int):
        return await self._query(
            f"SELECT RAW s.shopper_id FROM {self._ks('shoppers')} s WHERE s.engagement.last_app_open >= $d "
            f"ORDER BY s.engagement.last_app_open DESC LIMIT $n", d=since, n=int(limit))

    async def count(self, col):
        rows = await self._query(f"SELECT RAW COUNT(*) FROM {self._ks(col)}")
        return rows[0] if rows else 0


def make_store():
    return CouchbaseStore() if config.STORE_DRIVER == "couchbase" else MemoryStore()


store = make_store()
