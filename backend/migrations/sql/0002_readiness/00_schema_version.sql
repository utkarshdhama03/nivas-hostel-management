-- =============================================================================
-- 0002_readiness - expose ONLY the schema version to the app roles (Phase 1).
--
-- GET /api/v1/health/ready reports "not ready" when the database schema is older
-- than the code (docs/04 §12.1), by comparing alembic_version with the newest
-- migration file. The app roles deliberately have NO privileges on alembic_version
-- (Phase 0 invariant, tests/dbproof/test_schema.py), so instead of a grant this
-- function, owned by hms_owner, returns the version string and nothing else.
-- SECURITY DEFINER: runs with the owner's privileges; search_path pinned as for
-- every SECURITY DEFINER function in this schema.
-- =============================================================================
CREATE FUNCTION app_schema_version() RETURNS text
  LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path = pg_catalog, public
AS $$ SELECT version_num FROM public.alembic_version LIMIT 1 $$;

REVOKE ALL ON FUNCTION app_schema_version() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_schema_version() TO hms_app, hms_worker, hms_platform;
