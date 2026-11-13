#!/bin/bash
# =============================================================================
# Runs ONCE, when the database volume is first initialised (the official image
# executes every script in /docker-entrypoint-initdb.d on an empty data dir).
#
# Creates the five roles from docs/03 §2 and the application database.
# Everything inside the database (tables, RLS, grants) is created by Alembic,
# not here, so the same migration builds both the dev and the test databases.
#
# Passwords come from environment variables supplied by the hms-admin.env and
# hms-app.env files in the secrets folder outside the project. psql's :'var'
# syntax quotes them safely; they never appear in this file or in logs.
# =============================================================================
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
     -v owner_pw="$HMS_OWNER_PASSWORD" \
     -v app_pw="$HMS_APP_PASSWORD" \
     -v worker_pw="$HMS_WORKER_PASSWORD" \
     -v platform_pw="$HMS_PLATFORM_PASSWORD" \
     -v backup_pw="$HMS_BACKUP_PASSWORD" <<'SQL'
-- hms_owner: owns every object; used ONLY to run migrations.
CREATE ROLE hms_owner    LOGIN PASSWORD :'owner_pw'    NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
-- hms_app: the API. Subject to RLS, never owns tables.
CREATE ROLE hms_app      LOGIN PASSWORD :'app_pw'      NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
-- hms_worker: background jobs. Like hms_app plus job claiming and purges.
CREATE ROLE hms_worker   LOGIN PASSWORD :'worker_pw'   NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
-- hms_platform: Platform Owner module. NO BYPASSRLS: sees tenants only through
-- explicit policies and column grants (docs/03 §6).
CREATE ROLE hms_platform LOGIN PASSWORD :'platform_pw' NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
-- hms_backup: backup scripts (configured in Phase 10).
CREATE ROLE hms_backup   LOGIN PASSWORD :'backup_pw'   NOSUPERUSER NOCREATEROLE NOBYPASSRLS;

CREATE DATABASE hms OWNER hms_owner;
SQL

echo "bootstrap: roles and database 'hms' created"
