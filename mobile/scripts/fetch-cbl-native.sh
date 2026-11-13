#!/usr/bin/env bash
# npm does not clone cbl-reactnative git submodules (ios/cbl-js-swift, src/cblite-js).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$ROOT/node_modules/cbl-reactnative"

if [[ ! -d "$PKG/ios" ]]; then
  echo "cbl-reactnative not installed; skip native fetch"
  exit 0
fi

fetch() {
  local dest=$1 url=$2 pin=$3 marker=$4
  if [[ -f "$dest/$marker" ]]; then
    return 0
  fi
  rm -rf "$dest"
  git clone --quiet "$url" "$dest"
  git -C "$dest" checkout -q "$pin"
  echo "fetched $(basename "$dest")@$pin"
}

fetch "$PKG/ios/cbl-js-swift" \
  https://github.com/Couchbase-Ecosystem/cbl-js-swift.git \
  d2040e6a39a9bf40f085a3c790e08eedf7820a41 \
  DatabaseManager.swift

fetch "$PKG/src/cblite-js" \
  https://github.com/Couchbase-Ecosystem/cblite-js.git \
  063f796bb3a984df820065e4b63f7ef9ad9a5bf4 \
  cblite/index.ts

python3 "$ROOT/scripts/patch-cbl-rn-events.py"

