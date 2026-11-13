#!/usr/bin/env bash
# Run the Playwright end-to-end flows (Q87).
#   ./scripts/e2e.sh
#
# The flows run against their OWN database, hms_e2e, never the development database hms (the same
# idea as the backend tests' hms_test). Each run:
#   1. drops and recreates hms_e2e (and the upload folders of its old organizations), then migrates it;
#   2. restarts the API and worker pointed at hms_e2e, and checks that they really are;
#   3. runs the flows;
#   4. whatever happens, points the API and worker back at hms (and the registration limit back to 3).
# While it runs, http://localhost:5173 shows the throwaway e2e data, not yours.
# Finally it checks that hms has exactly as many organizations and Platform Owners as before.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
E2E_DB=hms_e2e

psql_su() { docker compose exec -T db psql -X -q -At -v ON_ERROR_STOP=1 -U postgres "$@"; }
dev_counts() { psql_su -d hms -c "SELECT (SELECT count(*) FROM organizations) || ' organizations, ' || (SELECT count(*) FROM platform_users) || ' Platform Owners'"; }

docker compose up -d --wait db mailpit
before="$(dev_counts)"

# Back to the development database when the script ends, whatever happens. The registration limit
# goes back to its default (3) at the same time.
restore() {
  echo "Pointing the API and worker back at the development database (hms)..."
  HMS_APP_DB=hms HMS_E2E_REGISTRATIONS=3 \
    docker compose up -d --force-recreate api worker >/dev/null 2>&1 || true
}
trap restore EXIT

# 1. A fresh hms_e2e. First remove the uploaded files of the previous run's organizations.
if [ "$(psql_su -d postgres -c "SELECT 1 FROM pg_database WHERE datname = '$E2E_DB'")" = "1" ]; then
  old_orgs="$(psql_su -d "$E2E_DB" -c "SELECT id FROM organizations" | tr '\n' ' ')"
  if [ -n "${old_orgs// /}" ]; then
    docker compose run --rm -T --no-deps tools sh -c 'cd /data/uploads && for id in "$@"; do rm -rf "./$id"; done' sh $old_orgs
  fi
fi
psql_su -d postgres -c "DROP DATABASE IF EXISTS $E2E_DB WITH (FORCE)"
psql_su -d postgres -c "CREATE DATABASE $E2E_DB OWNER hms_owner"
# Exported for every compose command from here on: api, worker and tools all use hms_e2e.
export HMS_APP_DB="$E2E_DB"
docker compose run --rm -T tools alembic upgrade head

# 2. The flows register more organizations than Q72 allows per IP per hour (all browser traffic
# reaches the API from the web container's address), so this run raises that limit. Exported so a
# service that depends on the API doesn't recreate it with the default.
export HMS_E2E_REGISTRATIONS=50
docker compose up -d --force-recreate api worker
docker compose up -d web
# Restart the dev server too: if its packages were reinstalled since it started (the quality gate
# runs npm ci in the same node_modules volume), its pre-bundled dependencies are stale and every
# page fails to load (504 "outdated optimize dep"). Then wait until it serves pages again.
docker compose restart web
for _ in $(seq 1 120); do
  docker compose logs web --since 2m 2>/dev/null | grep -q "ready in" && break
  sleep 1
done
for _ in $(seq 1 60); do
  docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready')" >/dev/null 2>&1 && break
  sleep 1
done
for service in api worker; do
  db="$(docker compose exec -T "$service" python -c "from app.core.config import get_settings; print(get_settings().hms_db_name)" | tr -d '\r')"
  if [ "$db" != "$E2E_DB" ]; then
    echo "STOP: the $service uses the database '$db', not $E2E_DB. Nothing was run."
    exit 1
  fi
done

# A throwaway Platform Owner for this run (Phase 9 flow), in hms_e2e. The password is random, lives
# only in this shell and the test process, and is never written to a file.
PO_EMAIL="po.e2e.$(date +%s)@example.com"
PO_PASSWORD="$(docker compose exec -T api python -c "import secrets; print('e2e ' + secrets.token_urlsafe(18))")"
docker compose exec -T -e PO_EMAIL="$PO_EMAIL" -e PO_PASSWORD="$PO_PASSWORD" api python -c "import os; from app.cli import create_platform_owner; create_platform_owner(os.environ['PO_EMAIL'], 'E2E Owner', os.environ['PO_PASSWORD'])"

# 3. The flows.
status=0
docker compose run --rm -e HMS_E2E_PO_EMAIL="$PO_EMAIL" -e HMS_E2E_PO_PASSWORD="$PO_PASSWORD" e2e || status=$?

# The development database must be exactly as it was.
after="$(dev_counts)"
if [ "$before" != "$after" ]; then
  echo "FAIL: the development database changed during the run: before $before, after $after"
  exit 1
fi
echo "Development database unchanged ($after)."
exit $status
