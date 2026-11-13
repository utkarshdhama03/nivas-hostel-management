#!/bin/bash
# =============================================================================
# The backup scheduler (docs/02 §12, docs/06 Phase 10). Runs inside the database container, next
# to the server, because pgBackRest must read the data directory. The loop runs as root only so
# the file copy can read the uploads (0600, owned by the API's user) and keep their ownership;
# every PostgreSQL and pgBackRest command runs as postgres through gosu.
#
# CATCH-UP SCHEDULING: this laptop is often asleep or off, so nothing here depends on clock
# times. At startup, and then every 15 minutes, each kind of backup compares its last
# SUCCESSFUL run in backup_runs with its interval and runs immediately if it is overdue
# (or has never succeeded). A machine that slept through the night backs up when it wakes.
#
#   full          weekly    pgBackRest full backup (the PITR base)
#   differential  daily     pgBackRest differential (needs a full first)
#   verify        daily     pgbackrest verify (checks the repository's checksums)
#   logical_dump  daily     pg_dump of "hms", encrypted, kept 14 days (independent of pgBackRest)
#   file_copy     daily     copy of the uploads volume to the backup disk (files are write-once)
#   restore_test  weekly    restore into a throwaway directory, start it, run sanity queries
#
# Every run is recorded in backup_runs (as hms_backup) and shown on the Platform health page.
# Secrets: the repository cipher pass comes from the environment (pgbackrest.env in the secrets
# folder) and is never written to a file or a log.
# =============================================================================
set -uo pipefail

REPO=/var/lib/pgbackrest
STANZA=hms
LOCATION="${HMS_BACKUP_LOCATION:-default_volume}"
PG="gosu postgres"
log() { echo "backup-loop: $*"; }

sql() {  # run SQL as hms_backup over the local socket; prints the result
  $PG psql -X -q -t -A -v ON_ERROR_STOP=1 -U hms_backup -d hms -c "$1"
}

start_run() {  # $1 = kind; prints the new run id
  sql "INSERT INTO backup_runs (kind, status) VALUES ('$1', 'running') RETURNING id" | head -1
}

finish_run() {  # $1 = id, $2 = succeeded|failed, $3 = size in bytes (or NULL), $4 = details JSON
  # The details go in as a psql variable (:'d' quotes them safely), never pasted into the SQL.
  # psql expands :'d' only in SQL read from stdin, not in -c.
  echo "UPDATE backup_runs SET status = '$2', finished_at = now(), size_bytes = $3, details = :'d'::jsonb WHERE id = '$1'" \
    | $PG psql -X -q -v ON_ERROR_STOP=1 -U hms_backup -d hms -v d="$4" >/dev/null
}

json_str() {  # stdin's last lines as a JSON string (at most 500 characters)
  printf '"%s"' "$(tail -5 | tr '\n\r\t' '   ' | tr -d '\000' | cut -c1-500 | sed 's/\\/\\\\/g; s/"/\\"/g')"
}

due() {  # $1 = kind(s) as a SQL list, $2 = interval; true when no success within the interval
  local n
  n=$(sql "SELECT count(*) FROM backup_runs WHERE kind IN ($1) AND status = 'succeeded' AND finished_at > now() - interval '$2'")
  [ "${n:-0}" = "0" ]
}

run() {  # $1 = kind, $2 = command whose last line of output is a size in bytes
  local kind=$1 id out rc size details
  id=$(start_run "$kind") || return 1
  out=$(bash -c "$2" 2>&1); rc=$?
  size=$(echo "$out" | tail -1 | grep -E '^[0-9]+$' || echo NULL)
  details=$(printf '{"location": "%s", "message": %s}' "$LOCATION" "$(echo "$out" | json_str)")
  if [ $rc -eq 0 ]; then
    finish_run "$id" succeeded "$size" "$details" && log "$kind ok"
  else
    finish_run "$id" failed NULL "$details"
    log "$kind FAILED (see backup_runs)"
  fi
  return $rc
}

ensure_stanza() {
  $PG pgbackrest --stanza=$STANZA --log-level-console=warn stanza-create >/dev/null 2>&1 \
    || $PG pgbackrest --stanza=$STANZA --log-level-console=warn check >/dev/null 2>&1
}

tick() {
  if due "'full'" "7 days"; then
    run full "$PG pgbackrest --stanza=$STANZA --type=full --log-level-console=warn backup && du -sb $REPO/backup | cut -f1"
  elif due "'full','differential'" "1 day"; then
    run differential "$PG pgbackrest --stanza=$STANZA --type=diff --log-level-console=warn backup && du -sb $REPO/backup | cut -f1"
  fi
  if due "'verify'" "1 day"; then
    run verify "$PG pgbackrest --stanza=$STANZA --log-level-console=warn verify && echo verified"
  fi
  if due "'logical_dump'" "1 day"; then
    run logical_dump "$PG /usr/local/bin/hms-dump.sh"
  fi
  if due "'file_copy'" "1 day"; then
    run file_copy "mkdir -p $REPO/files && cp -a -n /data/uploads/. $REPO/files/ && du -sb $REPO/files | cut -f1"
  fi
  if due "'restore_test'" "7 days"; then
    run restore_test "$PG /usr/local/bin/hms-restore-test.sh"
  fi
}

# Wait for the server and for the schema (backup_runs exists once migrations have run).
until $PG pg_isready -q -h /var/run/postgresql; do sleep 5; done
until [ "$(sql "SELECT to_regclass('public.backup_runs') IS NOT NULL" 2>/dev/null)" = "t" ]; do sleep 30; done
mkdir -p "$REPO"
chown postgres:postgres "$REPO"
ensure_stanza || log "stanza check failed; will retry"
log "started (location: $LOCATION); catching up any overdue backups now"
while true; do
  ensure_stanza >/dev/null 2>&1
  tick
  sleep 900
done
