-- =============================================================================
-- 0013_administrator_emails (D78): Organization sign-in with only an email and a password.
--
-- * administrator_emails: every staff account's email (since D76 that is the organization's one
--   Administrator, plus accounts D75/D76 closed), lower-cased, with its organization. The primary key
--   makes an Administrator email unique across the WHOLE platform, case-insensitive.
-- * A trigger on users keeps it in step (insert, email change). It runs as the owner.
-- * administrator_org(email) returns the organization ID for one exact email, or NULL: the only way
--   the application reads the table. Sign-in uses it before any tenant context exists.
-- * Row-Level Security stays FORCEd on the new table, with one policy for the owner role (what the
--   trigger and the function run as). hms_app, hms_worker and hms_platform get no privilege on it,
--   only EXECUTE on the function. No other table's RLS changes.
-- =============================================================================

CREATE TABLE administrator_emails (
  email_key  text PRIMARY KEY CHECK (email_key = lower(email_key) AND email_key LIKE '%@%'),
  org_id     uuid NOT NULL REFERENCES organizations (id),
  user_id    uuid NOT NULL UNIQUE REFERENCES users (id)
);
ALTER TABLE administrator_emails ENABLE ROW LEVEL SECURITY;
ALTER TABLE administrator_emails FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON administrator_emails TO hms_owner USING (true) WITH CHECK (true);
REVOKE ALL ON administrator_emails FROM PUBLIC;

-- The existing staff accounts (users is under FORCE RLS: one organization at a time). Two
-- organizations whose Administrators share an email make this fail: that is the rule.
DO $$
DECLARE org record;
BEGIN
  FOR org IN SELECT id FROM organizations ORDER BY id LOOP
    PERFORM set_config('app.org_id', org.id::text, true);
    INSERT INTO administrator_emails (email_key, org_id, user_id)
    SELECT lower(email), org_id, id FROM users WHERE kind = 'staff' AND email IS NOT NULL;
  END LOOP;
  PERFORM set_config('app.org_id', '', true);
END $$;

CREATE FUNCTION administrator_emails_sync() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = public, pg_temp
AS $$
BEGIN
  DELETE FROM administrator_emails WHERE user_id = NEW.id;
  IF NEW.kind = 'staff' AND NEW.email IS NOT NULL THEN
    INSERT INTO administrator_emails (email_key, org_id, user_id) VALUES (lower(NEW.email), NEW.org_id, NEW.id);
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION administrator_emails_sync() FROM PUBLIC;
CREATE TRIGGER users_administrator_emails AFTER INSERT OR UPDATE OF email, kind ON users
  FOR EACH ROW EXECUTE FUNCTION administrator_emails_sync();

CREATE FUNCTION administrator_org(p_email text) RETURNS uuid
  LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path = public, pg_temp
AS $$ SELECT org_id FROM administrator_emails WHERE email_key = lower(trim(p_email)) $$;
REVOKE ALL ON FUNCTION administrator_org(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION administrator_org(text) TO hms_app;
