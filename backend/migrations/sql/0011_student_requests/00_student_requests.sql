-- =============================================================================
-- 0011_student_requests (D74): students ask to register; the Administrator approves or rejects.
--
-- * organization_profiles.accepts_student_requests: the organization's switch, off by default.
-- * student_registration_requests: one row per request, organization-owned (org_id + RLS).
--   unverified -> pending (email confirmed) -> approved | rejected.
--   The photo is kept re-encoded in the row (bytea) until approval, when it becomes a normal
--   student_photo file. A rejected request's personal data is deleted after 30 days
--   (personal_data_deleted_at); unverified requests are deleted after 24 hours.
-- * auth_tokens: a new purpose 'student_request' for the email-verification link. Such a token
--   belongs to a request, not to a user, so user_id becomes optional for that purpose only.
--
-- Clock (D67): a security-style table (verification window, spam limits, purge): created_at,
-- email_verified_at, decided_at and personal_data_deleted_at use the real now().
-- =============================================================================

ALTER TABLE organization_profiles ADD COLUMN accepts_student_requests boolean NOT NULL DEFAULT false;

CREATE TABLE student_registration_requests (
  id                        uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id                    uuid NOT NULL REFERENCES organizations (id),
  hostel_id                 uuid NOT NULL,
  status                    text NOT NULL DEFAULT 'unverified'
                            CHECK (status IN ('unverified','pending','approved','rejected')),
  -- personal data (NULL only after the 30-day deletion of a rejected request)
  full_name                 text CHECK (length(full_name) BETWEEN 1 AND 120),
  email                     text CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  phone                     text CHECK (phone ~ '^\+?[0-9][0-9 -]{6,19}$'),
  gender                    text CHECK (gender IN ('male','female','other')),
  date_of_birth             date,
  program                   text CHECK (length(program) <= 80),
  year_of_study             smallint CHECK (year_of_study BETWEEN 1 AND 10),
  guardian_name             text CHECK (length(guardian_name) BETWEEN 1 AND 120),
  guardian_relationship     text CHECK (guardian_relationship IN ('father','mother','guardian','sibling','spouse','other')),
  guardian_phone            text CHECK (guardian_phone ~ '^\+?[0-9][0-9 -]{6,19}$'),
  password_hash             text,                     -- Argon2id; moves to the user at approval
  photo                     bytea CHECK (octet_length(photo) <= 400000),   -- re-encoded JPEG/PNG
  photo_mime                text CHECK (photo_mime IN ('image/jpeg','image/png')),
  -- workflow
  email_verified_at         timestamptz,
  decided_by                uuid,
  decided_at                timestamptz,
  reject_reason             text CHECK (length(reject_reason) BETWEEN 5 AND 500),
  student_id                uuid,
  personal_data_deleted_at  timestamptz,
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, hostel_id)  REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, decided_by) REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, student_id) REFERENCES students (org_id, id),
  CONSTRAINT student_requests_verified CHECK (status = 'unverified' OR email_verified_at IS NOT NULL),
  CONSTRAINT student_requests_decided CHECK
    ((status IN ('approved','rejected')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT student_requests_reason CHECK ((status = 'rejected') = (reject_reason IS NOT NULL)),
  CONSTRAINT student_requests_student CHECK ((status = 'approved') = (student_id IS NOT NULL)),
  -- an approved request hands its password and photo over: none stay in the row
  CONSTRAINT student_requests_approved_cleared CHECK
    (status <> 'approved' OR (password_hash IS NULL AND photo IS NULL)),
  -- personal data is complete until it is deleted, and only a rejected request's is deleted
  CONSTRAINT student_requests_personal_data CHECK (
    (personal_data_deleted_at IS NULL
       AND full_name IS NOT NULL AND email IS NOT NULL AND gender IS NOT NULL AND date_of_birth IS NOT NULL
       AND guardian_name IS NOT NULL AND guardian_relationship IS NOT NULL AND guardian_phone IS NOT NULL
       AND (status = 'approved' OR (password_hash IS NOT NULL AND photo IS NOT NULL AND photo_mime IS NOT NULL)))
    OR
    (personal_data_deleted_at IS NOT NULL AND status = 'rejected'
       AND full_name IS NULL AND email IS NULL AND phone IS NULL AND date_of_birth IS NULL
       AND guardian_name IS NULL AND guardian_phone IS NULL AND password_hash IS NULL AND photo IS NULL))
);
-- One open (unverified or pending) request per email per organization.
CREATE UNIQUE INDEX student_requests_one_open_per_email ON student_registration_requests (org_id, lower(email))
  WHERE status IN ('unverified','pending');
CREATE INDEX ON student_registration_requests (org_id, status, created_at DESC);
CREATE INDEX ON student_registration_requests (org_id, lower(email));

ALTER TABLE student_registration_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE student_registration_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON student_registration_requests
  USING (org_id = app_current_org())
  WITH CHECK (org_id = app_current_org());

GRANT SELECT, INSERT, UPDATE ON student_registration_requests TO hms_app, hms_worker;
GRANT DELETE ON student_registration_requests TO hms_worker;   -- the purge (unverified after 24 hours)

-- --- the verification link ---------------------------------------------------------------------
ALTER TABLE auth_tokens ADD COLUMN registration_request_id uuid;
ALTER TABLE auth_tokens ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE auth_tokens
  ADD CONSTRAINT auth_tokens_registration_request_fk
    FOREIGN KEY (org_id, registration_request_id) REFERENCES student_registration_requests (org_id, id);
ALTER TABLE auth_tokens DROP CONSTRAINT auth_tokens_purpose_check;
ALTER TABLE auth_tokens ADD CONSTRAINT auth_tokens_purpose_check
  CHECK (purpose IN ('activation','org_registration','student_request'));
ALTER TABLE auth_tokens ADD CONSTRAINT auth_tokens_owner_check CHECK (
  (purpose = 'student_request' AND registration_request_id IS NOT NULL AND user_id IS NULL)
  OR (purpose <> 'student_request' AND user_id IS NOT NULL AND registration_request_id IS NULL));
CREATE UNIQUE INDEX auth_tokens_one_live_request_uq ON auth_tokens (registration_request_id)
  WHERE used_at IS NULL AND revoked_at IS NULL AND registration_request_id IS NOT NULL;
