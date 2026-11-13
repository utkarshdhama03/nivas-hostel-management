#!/bin/bash
# Restore a pgBackRest backup into a throwaway directory, start it on a private port, run sanity
# queries, then remove it (docs/02 §12 "weekly automated restore test"). Never touches the live
# cluster.
#   hms-restore-test.sh                       latest backup, consistent point (weekly test)
#   hms-restore-test.sh "<timestamp>" "<sql>" point-in-time restore, then print the result of <sql>
#                                             (used by the PITR drill, scripts/drill-pitr.sh)
set -euo pipefail
TARGET="${1:-}"
QUERY="${2:-}"
DIR=$(mktemp -d /tmp/hms-restore.XXXXXX)
PORT=5499
cleanup() { pg_ctl -D "$DIR" -m immediate stop >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT

if [ -n "$TARGET" ]; then
  pgbackrest --stanza=hms --pg1-path="$DIR" --type=time --target="$TARGET" --target-action=promote \
    --log-level-console=warn restore
else
  pgbackrest --stanza=hms --pg1-path="$DIR" --type=immediate --target-action=promote \
    --log-level-console=warn restore
fi
# The copy must never archive into the real repository, and listens only on its own socket.
pg_ctl -D "$DIR" -w -t 300 -o "-p $PORT -c archive_mode=off -c listen_addresses='' -c unix_socket_directories=/tmp" \
  -l "$DIR/restore.log" start >/dev/null
q() { psql -X -q -t -A -h /tmp -p $PORT -U postgres -d "${2:-hms}" -c "$1"; }
until [ "$(q 'SELECT NOT pg_is_in_recovery()' postgres 2>/dev/null)" = "t" ]; do sleep 1; done
if [ -n "$QUERY" ]; then
  q "$QUERY" "${DRILL_DB:-hms}"
  exit 0
fi
TABLES=$(q "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
ORGS=$(q "SELECT count(*) FROM organizations")
VERSION=$(q "SELECT app_schema_version()")
[ "$TABLES" -gt 40 ] || { echo "only $TABLES tables restored"; exit 1; }
echo "restored: $TABLES tables, $ORGS organizations, schema $VERSION"
du -sb "$DIR" | cut -f1
