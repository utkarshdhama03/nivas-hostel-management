#!/usr/bin/env bash
# Drill 1 (docs/06 Phase 10): "restore the database to 10 minutes before a deliberately bad UPDATE
# and show the data is back", without touching the live database.
#   ./scripts/drill-pitr.sh            (stack running; at least one full backup taken)
#
# What it does, all inside a scratch database "hms_drill" on the live server:
#   1. creates a table with 100 rows of value 1, and archives the WAL;
#   2. waits, notes the time T, waits again;
#   3. runs the "bad" UPDATE that sets every value to 0, and archives the WAL;
#   4. restores the whole cluster to time T into a THROWAWAY directory (hms-restore-test.sh),
#      starts it on a private socket and sums the values there: 100 means the data is back;
#   5. drops the scratch database on the live server.
# The real restore procedure, which replaces the live cluster, is docs/runbooks/restore.md.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
WAIT="${DRILL_WAIT_SECONDS:-20}"   # the docs say "10 minutes"; any gap shows the same thing
psql_live() { docker compose exec -T db psql -X -q -t -A -U postgres "$@"; }

echo "1. scratch data: 100 rows with value 1"
psql_live -d postgres -c "DROP DATABASE IF EXISTS hms_drill" >/dev/null
psql_live -d postgres -c "CREATE DATABASE hms_drill" >/dev/null
psql_live -d hms_drill -c "CREATE TABLE t (v int); INSERT INTO t SELECT 1 FROM generate_series(1, 100)" >/dev/null
psql_live -d postgres -c "SELECT pg_switch_wal()" >/dev/null
docker compose exec -T -u postgres db pgbackrest --stanza=hms --log-level-console=warn backup --type=diff

sleep "$WAIT"
T=$(psql_live -d postgres -c "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '+00'")
echo "2. restore point T = $T"
sleep "$WAIT"

echo "3. the bad UPDATE"
psql_live -d hms_drill -c "UPDATE t SET v = 0" >/dev/null
psql_live -d postgres -c "SELECT pg_switch_wal()" >/dev/null
sleep 5
echo "   live sum now: $(psql_live -d hms_drill -c 'SELECT sum(v) FROM t')"

echo "4. point-in-time restore to T in a throwaway directory"
RESTORED=$(docker compose exec -T -u postgres -e DRILL_DB=hms_drill db \
  /usr/local/bin/hms-restore-test.sh "$T" "SELECT sum(v) FROM t" | tail -1)
echo "   restored sum at T: $RESTORED"

echo "5. cleaning up the scratch database"
psql_live -d postgres -c "DROP DATABASE hms_drill" >/dev/null
if [ "$RESTORED" = "100" ]; then echo "DRILL PASSED: the data is back as it was before the bad UPDATE"; else echo "DRILL FAILED"; exit 1; fi
