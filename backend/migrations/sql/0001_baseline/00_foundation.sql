-- =============================================================================
-- 00_foundation.sql - database-level access, extension, shared functions
-- (docs/03 §2, §3). Runs as hms_owner, who owns the database.
-- =============================================================================

-- --- Who may connect -----------------------------------------------------------
-- By default PUBLIC (every role) may connect to a new database. Remove that and
-- name the roles explicitly. format(%I) quotes the identifier safely; the
-- database name is the current one, so the same migration works for hms and
-- hms_test.
DO $$
BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO hms_app, hms_worker, hms_platform, hms_backup',
                 current_database());
END $$;

-- --- The public schema ---------------------------------------------------------
-- Only the owner creates objects; the app roles may only use what exists.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO hms_app, hms_worker, hms_platform, hms_backup;

-- --- Extension -----------------------------------------------------------------
-- btree_gist lets exclusion constraints combine "uuid =" with "range &&"
-- (no double booking, no overlapping leave or terms). It is a "trusted"
-- extension, so hms_owner can create it without superuser rights (docs/03 §3).
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- --- Current tenant ------------------------------------------------------------
-- Set per transaction by the app: SELECT set_config('app.org_id', '<uuid>', true).
-- NULLIF(..., ''): after a transaction ends, a custom setting already used on the
-- connection reads as '' (not NULL), and ''::uuid would raise an error.
CREATE FUNCTION app_current_org() RETURNS uuid
  LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$;

-- --- Append-only guard -----------------------------------------------------------
-- Attached as a BEFORE UPDATE/DELETE/TRUNCATE trigger to history tables. The app
-- roles already lack those privileges; this also stops the owner by accident.
CREATE FUNCTION forbid_change() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME
    USING ERRCODE = 'restrict_violation';
END $$;
