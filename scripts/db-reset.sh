#!/usr/bin/env bash
# Recreate the development database "hms" from scratch and apply all migrations.
# Destroys all data in "hms" (the test database is handled by pytest itself).
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1

read -r -p "This deletes ALL data in the development database 'hms'. Type 'reset' to continue: " answer
[ "$answer" = "reset" ] || { echo "Cancelled."; exit 1; }

docker compose up -d --wait db
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -c "DROP DATABASE IF EXISTS hms WITH (FORCE)" -c "CREATE DATABASE hms OWNER hms_owner"
docker compose run --rm tools alembic upgrade head
echo "Database 'hms' recreated at the latest migration."
