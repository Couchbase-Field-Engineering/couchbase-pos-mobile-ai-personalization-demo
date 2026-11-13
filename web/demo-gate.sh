#!/bin/sh
# Runs before nginx starts (the nginx image executes /docker-entrypoint.d/*.sh).
#
#   1. TLS: uses /etc/nginx/tls/tls.crt + tls.key if they are mounted (a real
#      certificate, or a Kubernetes TLS secret); otherwise generates a
#      self-signed pair there. TLS_HOSTS (comma-separated host names / IPs)
#      are added to the certificate's subjectAltName next to localhost.
#   2. Sign-in gate: writes the SHA-256 of "<DEMO_USERNAME>:<DEMO_PASSWORD>"
#      where nginx.conf compares it with the cookie the sign-in page sets.
#      The password itself is never written anywhere nginx serves.
set -eu

TLS_DIR=/etc/nginx/tls
if [ ! -s "$TLS_DIR/tls.crt" ] || [ ! -s "$TLS_DIR/tls.key" ]; then
  mkdir -p "$TLS_DIR"
  SAN="DNS:localhost,IP:127.0.0.1"
  CN=localhost
  for h in $(printf '%s' "${TLS_HOSTS:-}" | tr ',' ' '); do
    [ "$CN" = localhost ] && CN="$h"
    if printf '%s' "$h" | grep -Eq '^[0-9]+(\.[0-9]+){3}$'; then SAN="$SAN,IP:$h"; else SAN="$SAN,DNS:$h"; fi
  done
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -subj "/CN=$CN" -addext "subjectAltName=$SAN" \
    -keyout "$TLS_DIR/tls.key" -out "$TLS_DIR/tls.crt" 2>/dev/null
  chmod 600 "$TLS_DIR/tls.key"
  echo "demo-gate: generated a self-signed certificate for $SAN"
else
  echo "demo-gate: using the certificate in $TLS_DIR"
fi

USER_LC=$(printf '%s' "${DEMO_USERNAME:-demo}" | tr 'A-Z' 'a-z')
HASH=$(printf '%s:%s' "$USER_LC" "${DEMO_PASSWORD:?DEMO_PASSWORD is required}" | sha256sum | cut -d' ' -f1)
printf '"0:%s" 0;\n' "$HASH" > /etc/nginx/demo-gate.map
echo "demo-gate: sign-in required (user: $USER_LC)"
