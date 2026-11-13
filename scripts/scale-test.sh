#!/usr/bin/env bash
# Scale test (docs/06 Phase 10; Q3 target, Q79 synthetic data; NFR-12 budgets).
#   ./scripts/scale-test.sh                 50 organizations x 5,000 students, then timings
#   ./scripts/scale-test.sh --keep          keep the hms_scale database afterwards
#   ./scripts/scale-test.sh --bench-only    time again against an hms_scale kept earlier
#   SEED_ARGS="--orgs 5 --students 1000" ./scripts/scale-test.sh   a smaller try-out
#
# Writes only to a separate database, hms_scale, in the same PostgreSQL server, never hms or
# hms_test. Seeding the full size takes about 15 minutes and about 2 GB of disk. Because WAL
# archiving covers the whole server, the backup repository also grows for a while (until those
# WAL files leave the retention window). That is why hms_scale is dropped at the end unless --keep:
# otherwise every later backup and restore test would carry it.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
KEEP=0; SEED=1
for a in "$@"; do
  case "$a" in
    --keep) KEEP=1 ;;
    --bench-only) SEED=0; KEEP=1 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done
TOOLS=(docker compose run --rm -T -e HMS_DB_NAME=hms_scale tools)
docker compose up -d --wait db
if [ "$SEED" = 1 ]; then
  # shellcheck disable=SC2086
  "${TOOLS[@]}" python -m tests.scale.seed ${SEED_ARGS:-}
fi
status=0
"${TOOLS[@]}" python -m tests.scale.bench || status=$?
if [ "$KEEP" = 0 ]; then
  docker compose exec -T db psql -X -q -U postgres -d postgres -c "DROP DATABASE IF EXISTS hms_scale WITH (FORCE)"
  echo "hms_scale dropped (use --keep to keep it)"
fi
exit $status
