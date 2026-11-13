#!/usr/bin/env bash
# Behind a TLS-inspecting corporate proxy (Zscaler etc.), pip inside the backend
# image can't verify PyPI. This exports the proxy's root CA from the macOS
# keychain to backend/certs/corporate-ca.crt, which backend/Dockerfile installs.
# Leave the file empty (the default) when you're not behind such a proxy.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT=backend/certs/corporate-ca.crt
NAME="${CORPORATE_CA_NAME:-Zscaler Root CA}"
if security find-certificate -c "$NAME" -p /Library/Keychains/System.keychain > "$OUT" 2>/dev/null && [ -s "$OUT" ]; then
  echo "Exported '$NAME' to $OUT ($(wc -c < "$OUT") bytes). Rebuild: docker compose build backend"
else
  : > "$OUT"
  echo "No certificate named '$NAME' in the System keychain; left $OUT empty (set CORPORATE_CA_NAME to override)."
fi
