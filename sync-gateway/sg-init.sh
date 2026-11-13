#!/bin/sh
# Creates (or updates) the `pos` Sync Gateway database over the admin API.
set -u
SG="${SG_ADMIN_URL:-http://sync-gateway:4985}"
AUTH="${SG_ADMIN_USERNAME:-Administrator}:${SG_ADMIN_PASSWORD:-password123}"
DB="${SG_DB:-pos}"
echo "Waiting for Sync Gateway admin API at $SG ..."
until curl -s -o /dev/null -u "$AUTH" "$SG/"; do sleep 3; done
for i in $(seq 1 30); do
  code=$(curl -s -o /tmp/out -w '%{http_code}' -u "$AUTH" -X PUT "$SG/$DB/" -H 'Content-Type: application/json' --data-binary @/sg/db-config.json)
  case "$code" in
    201|200) echo "Created database $DB"; exit 0 ;;
    412) code=$(curl -s -o /tmp/out -w '%{http_code}' -u "$AUTH" -X PUT "$SG/$DB/_config" -H 'Content-Type: application/json' --data-binary @/sg/db-config.json)
         [ "$code" = "201" ] || [ "$code" = "200" ] && { echo "Updated database $DB config"; exit 0; } ;;
  esac
  echo "  attempt $i: HTTP $code $(cat /tmp/out)"; sleep 5
done
exit 1
