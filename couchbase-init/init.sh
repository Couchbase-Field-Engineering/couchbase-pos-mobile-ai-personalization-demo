#!/usr/bin/env bash
# One-shot Couchbase Server setup for the demo: cluster, bucket `pos`,
# scope `retail`, its collections and the indexes the backend queries.
# Idempotent - safe to re-run on every `docker compose up`.
set -uo pipefail
H="${CB_HOST:-couchbase}"
U="${CB_USERNAME:-Administrator}"
P="${CB_PASSWORD:-password123}"
B="${CB_BUCKET:-pos}"
S="${CB_SCOPE:-retail}"
COLLECTIONS="shoppers stores recommendations orders assistant context_cache catalog inventory lookups"

echo "Waiting for Couchbase at $H:8091 ..."
until curl -s -o /dev/null "http://$H:8091/pools"; do sleep 3; done

couchbase-cli cluster-init -c "$H" --cluster-username "$U" --cluster-password "$P" \
  --services data,index,query --cluster-ramsize "${CB_RAMSIZE:-1280}" --cluster-index-ramsize "${CB_INDEX_RAMSIZE:-512}" \
  --index-storage-setting default 2>&1 | grep -v "already initialized" || true

until couchbase-cli server-list -c "$H" -u "$U" -p "$P" >/dev/null 2>&1; do sleep 2; done

if ! couchbase-cli bucket-list -c "$H" -u "$U" -p "$P" | grep -qx "$B"; then
  couchbase-cli bucket-create -c "$H" -u "$U" -p "$P" --bucket "$B" --bucket-type couchbase \
    --bucket-ramsize "${CB_BUCKET_RAMSIZE:-1024}" --bucket-replica 0 --wait
fi

couchbase-cli collection-manage -c "$H" -u "$U" -p "$P" --bucket "$B" --create-scope "$S" 2>/dev/null || true
for c in $COLLECTIONS; do
  couchbase-cli collection-manage -c "$H" -u "$U" -p "$P" --bucket "$B" --create-collection "$S.$c" 2>/dev/null || true
done

q() {
  for i in $(seq 1 40); do
    out=$(curl -s -u "$U:$P" "http://$H:8093/query/service" --data-urlencode "statement=$1")
    if echo "$out" | grep -q '"status": *"success"'; then echo "  ok: $1"; return 0; fi
    sleep 3
  done
  echo "  FAILED: $1 -> $out"; return 1
}
echo "Creating indexes ..."
q "CREATE INDEX idx_orders_status IF NOT EXISTS ON \`$B\`.\`$S\`.orders(type, status, created_at)"
q "CREATE INDEX idx_orders_shopper IF NOT EXISTS ON \`$B\`.\`$S\`.orders(type, shopper_id, created_at)"
q "CREATE INDEX idx_shoppers_active IF NOT EXISTS ON \`$B\`.\`$S\`.shoppers(engagement.last_app_open, shopper_id)"
for c in $COLLECTIONS; do q "CREATE PRIMARY INDEX IF NOT EXISTS ON \`$B\`.\`$S\`.\`$c\`"; done
echo "Couchbase ready: bucket $B, scope $S, collections: $COLLECTIONS"
