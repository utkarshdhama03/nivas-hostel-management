-- =============================================================================
-- 0014_student_emails (D80): Student sign-in with only an email and a password.
--
-- * Every student has an email (CHECK on users). Students without one (the in-person activation
--   path, Q69) are gone with that path.
-- * student_emails: every student's email, lower-cased, with its organization. The primary key makes
--   a student email unique across the WHOLE platform, case-insensitive. Kept in step by the same
--   trigger as administrator_emails (D78), now handling both kinds of account.
-- * student_request_emails: the email of every registration request that can still be told about at
--   sign-in (unverified, pending, rejected with its password), so Student sign-in can find the
--   organization of someone who asked to register (D74's messages). Several organizations may hold a
--   request for one email; the newest wins. Kept in step by a trigger on the requests table.
-- * student_org(email): the organization of that student or, failing that, of the newest such
--   request; NULL otherwise. The only way the application reads either table.
-- * Both tables keep RLS FORCEd with one owner-only policy; the application roles have no privilege
--   on them, only EXECUTE on the function. No other table's RLS changes.
-- =============================================================================

-- Every student has an email. Existing students without one stop the migration: that is the rule.
ALTER TABLE users ADD CONSTRAINT users_student_email_required CHECK (kind <> 'student' OR email IS NOT NULL);

CREATE TABLE student_emails (
  email_key  text PRIMARY KEY CHECK (email_key = lower(email_key) AND email_key LIKE '%@%'),
  org_id     uuid NOT NULL REFERENCES organizations (id),
  user_id    uuid NOT NULL UNIQUE REFERENCES users (id)
);
CREATE TABLE student_request_emails (
  request_id  uuid PRIMARY KEY,
  email_key   text NOT NULL CHECK (email_key = lower(email_key)),
  org_id      uuid NOT NULL REFERENCES organizations (id),
  created_at  timestamptz NOT NULL,
  FOREIGN KEY (org_id, request_id) REFERENCES student_registration_requests (org_id, id) ON DELETE CASCADE
);
CREATE INDEX ON student_request_emails (email_key, created_at DESC);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['student_emails', 'student_request_emails'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY owner_only ON %I TO hms_owner USING (true) WITH CHECK (true)', t);
    EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
  END LOOP;
END $$;

-- The existing students and requests (users and requests are under FORCE RLS: one organization at a time).
DO $$
DECLARE org record;
BEGIN
  FOR org IN SELECT id FROM organizations ORDER BY id LOOP
    PERFORM set_config('app.org_id', org.id::text, true);
    INSERT INTO student_emails (email_key, org_id, user_id)
    SELECT lower(email), org_id, id FROM users WHERE kind = 'student';
    INSERT INTO student_request_emails (request_id, email_key, org_id, created_at)
    SELECT id, lower(email), org_id, created_at FROM student_registration_requests
     WHERE password_hash IS NOT NULL AND status IN ('unverified', 'pending', 'rejected');
  END LOOP;
  PERFORM set_config('app.org_id', '', true);
END $$;

-- One trigger function for both kinds of account (replaces D78's).
CREATE OR REPLACE FUNCTION administrator_emails_sync() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = public, pg_temp
AS $$
BEGIN
  DELETE FROM administrator_emails WHERE user_id = NEW.id;
  DELETE FROM student_emails WHERE user_id = NEW.id;
  IF NEW.email IS NOT NULL AND NEW.kind = 'staff' THEN
    INSERT INTO administrator_emails (email_key, org_id, user_id) VALUES (lower(NEW.email), NEW.org_id, NEW.id);
  ELSIF NEW.email IS NOT NULL AND NEW.kind = 'student' THEN
    INSERT INTO student_emails (email_key, org_id, user_id) VALUES (lower(NEW.email), NEW.org_id, NEW.id);
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION student_request_emails_sync() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = public, pg_temp
AS $$
BEGIN
  DELETE FROM student_request_emails WHERE request_id = NEW.id;
  IF NEW.email IS NOT NULL AND NEW.password_hash IS NOT NULL AND NEW.status IN ('unverified', 'pending', 'rejected') THEN
    INSERT INTO student_request_emails (request_id, email_key, org_id, created_at)
    VALUES (NEW.id, lower(NEW.email), NEW.org_id, NEW.created_at);
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION student_request_emails_sync() FROM PUBLIC;
CREATE TRIGGER student_requests_sign_in_emails AFTER INSERT OR UPDATE ON student_registration_requests
  FOR EACH ROW EXECUTE FUNCTION student_request_emails_sync();

CREATE FUNCTION student_org(p_email text) RETURNS uuid
  LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path = public, pg_temp
AS $$
  SELECT coalesce(
    (SELECT org_id FROM student_emails WHERE email_key = lower(trim(p_email))),
    (SELECT org_id FROM student_request_emails WHERE email_key = lower(trim(p_email))
      ORDER BY created_at DESC LIMIT 1))
$$;
REVOKE ALL ON FUNCTION student_org(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION student_org(text) TO hms_app;

-- Whether an email belongs to a student anywhere (admission and registration refuse it, D80): a
-- yes/no, no organization.
CREATE FUNCTION student_email_taken(p_email text) RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path = public, pg_temp
AS $$ SELECT EXISTS (SELECT 1 FROM student_emails WHERE email_key = lower(trim(p_email))) $$;
REVOKE ALL ON FUNCTION student_email_taken(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION student_email_taken(text) TO hms_app;
