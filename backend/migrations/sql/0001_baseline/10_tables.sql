-- =============================================================================
-- 10_tables.sql - every table, constraint and index (docs/03 §5).
-- Extracted from docs/03-database-design.md §5 on 2026-11-07. From Phase 0 on,
-- this file is the source of truth; docs/03 mirrors it (change log: docs/03 §13).
-- =============================================================================

-- ---------------------------------------------------------------- 5.1 Platform (no organization; no RLS; `hms_app` has no access)
CREATE TABLE platform_users (
  id                     uuid PRIMARY KEY DEFAULT uuidv7(),
  email                  text NOT NULL CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  full_name              text NOT NULL CHECK (length(full_name) BETWEEN 1 AND 120),
  password_hash          text NOT NULL,
  totp_secret_encrypted  bytea,                 -- enrolled at first login; TOTP is mandatory (Q16)
  totp_key_id            text CHECK (totp_key_id ~ '^[a-z0-9-]{1,32}$'),   -- data key that encrypted the secret (Q65)
  totp_enabled_at        timestamptz,
  totp_last_used_step    bigint CHECK (totp_last_used_step >= 0),   -- blocks reuse of a TOTP code (05 AUTH-7)
  status                 text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  failed_login_count     smallint NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
  locked_until           timestamptz,
  last_login_at          timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  CHECK (totp_enabled_at IS NULL OR totp_secret_encrypted IS NOT NULL),
  CHECK ((totp_secret_encrypted IS NULL) = (totp_key_id IS NULL))
);
CREATE UNIQUE INDEX platform_users_email_uq ON platform_users (lower(email));

CREATE TABLE platform_recovery_codes (
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  platform_user_id  uuid NOT NULL REFERENCES platform_users (id),
  code_hash         text NOT NULL,
  used_at           timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (platform_user_id, code_hash)
);

CREATE TABLE platform_sessions (
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  platform_user_id     uuid NOT NULL REFERENCES platform_users (id),
  family_id            uuid NOT NULL,           -- all rotations of one login share this
  refresh_token_hash   text NOT NULL UNIQUE,
  mfa_verified_at      timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  last_used_at         timestamptz,
  expires_at           timestamptz NOT NULL,
  absolute_expires_at  timestamptz NOT NULL,
  revoked_at           timestamptz,
  revoke_reason        text CHECK (revoke_reason IN ('logout','rotated','reuse_detected','admin','expired')),
  ip_address           inet,
  user_agent           text,
  CHECK (expires_at <= absolute_expires_at),
  CHECK ((revoked_at IS NULL) = (revoke_reason IS NULL))
);
CREATE INDEX ON platform_sessions (platform_user_id) WHERE revoked_at IS NULL;
CREATE INDEX ON platform_sessions (family_id);

CREATE TABLE platform_settings (
  key         text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_.]{2,63}$'),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  value       jsonb NOT NULL,
  updated_by  uuid NOT NULL REFERENCES platform_users (id),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE backup_runs (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  kind         text NOT NULL CHECK (kind IN ('full','differential','logical_dump','file_copy','verify','restore_test')),
  status       text NOT NULL CHECK (status IN ('running','succeeded','failed')),
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  size_bytes   bigint CHECK (size_bytes >= 0),
  details      jsonb NOT NULL DEFAULT '{}',
  CHECK ((status = 'running') = (finished_at IS NULL))
);
CREATE INDEX ON backup_runs (kind, started_at DESC);

-- ---------------------------------------------------------------- 5.2 Organizations
CREATE TABLE organizations (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  slug               text NOT NULL UNIQUE
                     CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$')            -- 3–40 chars (Q8)
                     CHECK (slug NOT IN ('admin','api','app','platform','portal','static','www')),
  name               text NOT NULL CHECK (length(name) BETWEEN 2 AND 120),
  status             text NOT NULL DEFAULT 'pending_verification'
                     CHECK (status IN ('pending_verification','active','suspended')),
  timezone           text NOT NULL DEFAULT 'Asia/Kolkata',                           -- validated by the app (Q48)
  email_verified_at  timestamptz,
  suspended_at       timestamptz,
  suspension_reason  text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CHECK (status = 'pending_verification' OR email_verified_at IS NOT NULL),
  CHECK ((status = 'suspended') = (suspended_at IS NOT NULL)),
  CHECK (suspended_at IS NULL OR suspension_reason IS NOT NULL)
);
CREATE INDEX ON organizations (status);

-- Only these status changes are allowed, and suspension is Platform-Owner-only (Q13, Q14)
CREATE FUNCTION organizations_status_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF OLD.status = 'pending_verification' AND NEW.status = 'active' THEN
      RETURN NEW;                                            -- email verification
    ELSIF current_user = 'hms_platform'
      AND (OLD.status, NEW.status) IN (('active','suspended'), ('suspended','active')) THEN
      RETURN NEW;                                            -- suspend / reactivate
    END IF;
    RAISE EXCEPTION 'organization status change % -> % is not allowed', OLD.status, NEW.status;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER organizations_status_guard BEFORE UPDATE ON organizations
  FOR EACH ROW EXECUTE FUNCTION organizations_status_guard();

CREATE TABLE organization_profiles (
  org_id          uuid PRIMARY KEY REFERENCES organizations (id),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  legal_name      text,
  contact_email   text NOT NULL CHECK (contact_email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  contact_phone   text CHECK (contact_phone ~ '^\+?[0-9][0-9 -]{6,19}$'),
  address_line1   text,
  address_line2   text,
  city            text,
  state           text,
  postal_code     text CHECK (postal_code ~ '^[0-9]{6}$'),
  upi_id          text CHECK (upi_id ~ '^[A-Za-z0-9._-]{2,255}@[A-Za-z]{2,64}$'),   -- Q28
  upi_payee_name  text,
  settings        jsonb NOT NULL DEFAULT '{}',     -- UI-level preferences; schema validated by Pydantic
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (upi_id IS NULL OR upi_payee_name IS NOT NULL)
);

CREATE TABLE academic_sessions (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id      uuid NOT NULL REFERENCES organizations (id),
  name        text NOT NULL CHECK (length(name) BETWEEN 2 AND 40),     -- e.g. '2026-27' (Q18)
  starts_on   date NOT NULL,
  ends_on     date NOT NULL,
  is_current  boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, name),
  CHECK (ends_on > starts_on)
);
CREATE UNIQUE INDEX academic_sessions_one_current_uq ON academic_sessions (org_id) WHERE is_current;

CREATE TABLE academic_terms (                      -- needed for the 'term' billing cycle (Q24)
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id               uuid NOT NULL REFERENCES organizations (id),
  academic_session_id  uuid NOT NULL,
  name                 text NOT NULL CHECK (length(name) BETWEEN 2 AND 40),
  starts_on            date NOT NULL,
  ends_on              date NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (academic_session_id, name),
  FOREIGN KEY (org_id, academic_session_id) REFERENCES academic_sessions (org_id, id),
  CHECK (ends_on > starts_on),
  EXCLUDE USING gist (academic_session_id WITH =, (daterange(starts_on, ends_on, '[]')) WITH &&)
);

CREATE TABLE org_counters (                        -- gap-free numbering (receipts Q29, student/employee codes)
  org_id      uuid NOT NULL REFERENCES organizations (id),
  name        text NOT NULL CHECK (name IN ('receipt','student_code','employee_code')),
  scope_key   text NOT NULL CHECK (scope_key ~ '^[0-9]{4}(-[0-9]{2})?$'),   -- '2026-27' or '2026'
  last_value  bigint NOT NULL DEFAULT 0 CHECK (last_value >= 0),
  PRIMARY KEY (org_id, name, scope_key)
);

-- ---------------------------------------------------------------- 5.3 Hostel structure
CREATE TABLE hostels (
  id                              uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id                          uuid NOT NULL REFERENCES organizations (id),
  code                            text NOT NULL CHECK (code ~ '^[A-Z0-9-]{1,12}$'),
  name                            text NOT NULL CHECK (length(name) BETWEEN 2 AND 120),
  resident_type                   text CHECK (resident_type IN ('boys','girls','coed')),   -- Q19
  address                         text,
  curfew_time                     time,                                                     -- Q35
  attendance_window_start         time NOT NULL DEFAULT '21:00',                            -- Q32 (PROPOSAL default)
  attendance_window_length        interval NOT NULL DEFAULT '2 hours'
                                  CHECK (attendance_window_length > interval '0' AND attendance_window_length <= interval '12 hours'),
  local_outing_requires_approval  boolean NOT NULL DEFAULT true,                            -- Q35
  gate_exit_buffer                interval NOT NULL DEFAULT '2 hours' CHECK (gate_exit_buffer >= interval '0'),   -- Q37
  gate_return_grace               interval NOT NULL DEFAULT '1 hour'  CHECK (gate_return_grace >= interval '0'),  -- Q37
  is_active                       boolean NOT NULL DEFAULT true,
  created_at                      timestamptz NOT NULL DEFAULT now(),
  updated_at                      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, code),
  UNIQUE (org_id, name)
);

CREATE TABLE buildings (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id      uuid NOT NULL REFERENCES organizations (id),
  hostel_id   uuid NOT NULL,
  name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostel_id, id),
  UNIQUE (org_id, hostel_id, name),
  FOREIGN KEY (org_id, hostel_id) REFERENCES hostels (org_id, id)
);

CREATE TABLE floors (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id       uuid NOT NULL REFERENCES organizations (id),
  hostel_id    uuid NOT NULL,
  building_id  uuid NOT NULL,
  level        smallint NOT NULL CHECK (level BETWEEN -5 AND 200),
  name         text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostel_id, id),
  UNIQUE (building_id, level),
  FOREIGN KEY (org_id, hostel_id, building_id) REFERENCES buildings (org_id, hostel_id, id)
);

CREATE TABLE room_types (                                       -- Q20
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id         uuid NOT NULL REFERENCES organizations (id),
  hostel_id      uuid NOT NULL,
  name           text NOT NULL CHECK (length(name) BETWEEN 2 AND 60),   -- e.g. 'Double, AC'
  description    text,
  beds_per_room  smallint NOT NULL CHECK (beds_per_room BETWEEN 1 AND 20),
  is_active      boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostel_id, id),
  UNIQUE (org_id, hostel_id, name),
  FOREIGN KEY (org_id, hostel_id) REFERENCES hostels (org_id, id)
);

CREATE TABLE rooms (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id        uuid NOT NULL REFERENCES organizations (id),
  hostel_id     uuid NOT NULL,
  floor_id      uuid NOT NULL,
  room_type_id  uuid NOT NULL,
  number        text NOT NULL CHECK (number ~ '^[A-Za-z0-9-]{1,10}$'),
  status        text NOT NULL DEFAULT 'available' CHECK (status IN ('available','maintenance','inactive')),
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostel_id, id),
  UNIQUE (floor_id, number),
  FOREIGN KEY (org_id, hostel_id, floor_id)     REFERENCES floors (org_id, hostel_id, id),
  FOREIGN KEY (org_id, hostel_id, room_type_id) REFERENCES room_types (org_id, hostel_id, id)
);
CREATE INDEX ON rooms (org_id, hostel_id, status);
CREATE INDEX ON rooms (org_id, room_type_id);

CREATE TABLE beds (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id      uuid NOT NULL REFERENCES organizations (id),
  hostel_id   uuid NOT NULL,
  room_id     uuid NOT NULL,
  label       text NOT NULL CHECK (label ~ '^[A-Za-z0-9]{1,4}$'),     -- 'A', 'B', '1'
  status      text NOT NULL DEFAULT 'available' CHECK (status IN ('available','maintenance','inactive')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostel_id, id),
  UNIQUE (room_id, label),
  FOREIGN KEY (org_id, hostel_id, room_id) REFERENCES rooms (org_id, hostel_id, id)
);
CREATE INDEX ON beds (org_id, hostel_id, status);

-- ---------------------------------------------------------------- 5.4 Users, authentication and roles
CREATE TABLE users (
  id                     uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id                 uuid NOT NULL REFERENCES organizations (id),
  kind                   text NOT NULL CHECK (kind IN ('staff','student')),
  full_name              text NOT NULL CHECK (length(full_name) BETWEEN 1 AND 120),
  email                  text CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),   -- optional (copied activation links, Q2)
  email_verified_at      timestamptz,
  phone                  text CHECK (phone ~ '^\+?[0-9][0-9 -]{6,19}$'),
  password_hash          text,                    -- NULL until activation
  status                 text NOT NULL DEFAULT 'pending_activation'
                         CHECK (status IN ('pending_activation','active','read_only','deactivated')),
  read_only_until        date,                    -- exit window (Q22)
  totp_secret_encrypted  bytea,                   -- Q16 / Q56
  totp_key_id            text CHECK (totp_key_id ~ '^[a-z0-9-]{1,32}$'),   -- data key that encrypted the secret (Q65)
  totp_enabled_at        timestamptz,
  totp_last_used_step    bigint CHECK (totp_last_used_step >= 0),   -- blocks reuse of a TOTP code (05 AUTH-7)
  email_otp_enabled      boolean NOT NULL DEFAULT false,
  failed_login_count     smallint NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
  locked_until           timestamptz,
  last_login_at          timestamptz,
  created_by             uuid,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, created_by) REFERENCES users (org_id, id),
  CHECK (status NOT IN ('active','read_only') OR password_hash IS NOT NULL),
  CHECK ((status = 'read_only') = (read_only_until IS NOT NULL)),
  CHECK (totp_enabled_at IS NULL OR totp_secret_encrypted IS NOT NULL),
  CHECK ((totp_secret_encrypted IS NULL) = (totp_key_id IS NULL)),
  CHECK (NOT email_otp_enabled OR email IS NOT NULL)
);
CREATE UNIQUE INDEX users_org_email_uq ON users (org_id, lower(email)) WHERE email IS NOT NULL;   -- Q9
CREATE INDEX ON users (org_id, kind, status);

CREATE TABLE user_recovery_codes (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id      uuid NOT NULL REFERENCES organizations (id),
  user_id     uuid NOT NULL,
  code_hash   text NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, code_hash),
  FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
);

CREATE TABLE sessions (
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id               uuid NOT NULL REFERENCES organizations (id),
  user_id              uuid NOT NULL,
  family_id            uuid NOT NULL,
  refresh_token_hash   text NOT NULL UNIQUE,
  mfa_verified_at      timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  last_used_at         timestamptz,
  expires_at           timestamptz NOT NULL,
  absolute_expires_at  timestamptz NOT NULL,
  revoked_at           timestamptz,
  revoke_reason        text CHECK (revoke_reason IN ('logout','rotated','reuse_detected','admin','deactivated','expired')),
  ip_address           inet,
  user_agent           text,
  FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id),
  CHECK (expires_at <= absolute_expires_at),
  CHECK ((revoked_at IS NULL) = (revoke_reason IS NULL))
);
CREATE INDEX ON sessions (org_id, user_id) WHERE revoked_at IS NULL;
CREATE INDEX ON sessions (org_id, family_id);

CREATE TABLE auth_tokens (                         -- activation, password reset, org registration
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id      uuid NOT NULL REFERENCES organizations (id),
  user_id     uuid NOT NULL,
  purpose     text NOT NULL CHECK (purpose IN ('activation','password_reset','org_registration')),
  token_hash  text NOT NULL UNIQUE,
  delivery    text NOT NULL CHECK (delivery IN ('email','copied_link')),
  issued_by   uuid,                                -- the admin who copied the link (Q2)
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, user_id)   REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, issued_by) REFERENCES users (org_id, id),
  CHECK (expires_at > created_at),
  CHECK (used_at IS NULL OR revoked_at IS NULL),
  CHECK ((delivery = 'copied_link') = (issued_by IS NOT NULL)),
  CHECK (delivery <> 'copied_link' OR purpose = 'activation')      -- only activation links may be copied
);
CREATE UNIQUE INDEX auth_tokens_one_live_uq ON auth_tokens (user_id, purpose)
  WHERE used_at IS NULL AND revoked_at IS NULL;

CREATE TABLE email_otps (                          -- optional 2FA for non-admin roles (Q16)
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id       uuid NOT NULL REFERENCES organizations (id),
  user_id      uuid NOT NULL,
  code_hash    text NOT NULL,
  expires_at   timestamptz NOT NULL,
  attempts     smallint NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
  consumed_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id),
  CHECK (expires_at > created_at)
);
CREATE INDEX ON email_otps (org_id, user_id, created_at DESC);

CREATE TABLE login_attempts (
  id               uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id           uuid NOT NULL REFERENCES organizations (id),
  user_id          uuid,                           -- NULL when the identifier matched nobody
  identifier_hash  text NOT NULL,                  -- hash of what was typed, never the raw value
  ip_address       inet,
  succeeded        boolean NOT NULL,
  attempted_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
);
CREATE INDEX ON login_attempts (org_id, identifier_hash, attempted_at DESC);
CREATE INDEX ON login_attempts (org_id, ip_address, attempted_at DESC);
CREATE INDEX ON login_attempts (attempted_at);   -- purge

CREATE TABLE staff_members (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id         uuid NOT NULL REFERENCES organizations (id),
  user_id        uuid NOT NULL,
  employee_code  text NOT NULL CHECK (employee_code ~ '^[A-Z0-9][A-Z0-9/-]{1,29}$'),   -- stored upper-case
  designation    text,
  joined_on      date,
  left_on        date,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, employee_code),
  UNIQUE (org_id, user_id),
  FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id),
  CHECK (left_on IS NULL OR joined_on IS NULL OR left_on >= joined_on)
);

CREATE TABLE role_assignments (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id      uuid NOT NULL REFERENCES organizations (id),
  user_id     uuid NOT NULL,
  role        text NOT NULL CHECK (role IN ('super_admin','hostel_manager','warden','accountant','security_guard')),
  hostel_id   uuid,                                -- NULL = organization-wide (Q11)
  granted_by  uuid,                                -- NULL only for the Super Admin created at registration
  granted_at  timestamptz NOT NULL DEFAULT now(),
  revoked_by  uuid,
  revoked_at  timestamptz,
  FOREIGN KEY (org_id, user_id)    REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, hostel_id)  REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, granted_by) REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, revoked_by) REFERENCES users (org_id, id),
  CHECK (role <> 'super_admin' OR hostel_id IS NULL),            -- Super Admins are always org-wide (Q59)
  CHECK (granted_by IS NULL OR granted_by <> user_id),           -- nobody grants themselves a role
  CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
);
CREATE UNIQUE INDEX role_assignments_active_uq
  ON role_assignments (org_id, user_id, role, hostel_id) NULLS NOT DISTINCT
  WHERE revoked_at IS NULL;
CREATE INDEX ON role_assignments (org_id, hostel_id, role) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------- 5.5 Files
CREATE TABLE files (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id         uuid NOT NULL REFERENCES organizations (id),
  category       text NOT NULL CHECK (category IN
                   ('student_photo','identity_document','admission_document','payment_proof','complaint_attachment')),
  storage_key    text NOT NULL UNIQUE
                 CHECK (storage_key ~ '^[0-9a-f-]{36}/[a-z_]+/[0-9]{4}/[0-9]{2}/[0-9a-f-]{36}$'),  -- no "../" possible
  original_name  text NOT NULL CHECK (length(original_name) BETWEEN 1 AND 255),
  mime_type      text NOT NULL CHECK (mime_type IN ('image/jpeg','image/png','image/webp','application/pdf')),
  size_bytes     bigint NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 10485760),          -- 10 MB
  sha256         bytea NOT NULL CHECK (octet_length(sha256) = 32),
  uploaded_by    uuid NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  purged_at      timestamptz,
  access_level   text GENERATED ALWAYS AS
                   (CASE WHEN category = 'identity_document' THEN 'restricted' ELSE 'standard' END) STORED,   -- Q62
  encryption_key_id  text CHECK (encryption_key_id ~ '^[a-z0-9-]{1,32}$'),   -- data key used; NULL = not encrypted (Q65)
  UNIQUE (org_id, id),
  UNIQUE (org_id, id, category, mime_type),       -- lets student_documents pin the file's category and type
  FOREIGN KEY (org_id, uploaded_by) REFERENCES users (org_id, id),
  CHECK (category <> 'student_photo' OR mime_type LIKE 'image/%'),
  CHECK (purged_at IS NULL OR deleted_at IS NOT NULL),
  CHECK ((category = 'identity_document') = (encryption_key_id IS NOT NULL))     -- identity documents always encrypted (Q65)
);
CREATE INDEX ON files (deleted_at) WHERE deleted_at IS NOT NULL AND purged_at IS NULL;   -- purge job

-- ---------------------------------------------------------------- 5.6 Students
CREATE TABLE students (
  id                    uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id                uuid NOT NULL REFERENCES organizations (id),
  user_id               uuid NOT NULL,
  student_code          text NOT NULL CHECK (student_code ~ '^[A-Z0-9][A-Z0-9/-]{2,29}$'),   -- STU-2
  hostel_id             uuid NOT NULL,              -- current hostel (admitted hostel before first allocation)
  admission_session_id  uuid NOT NULL,
  gender                text NOT NULL CHECK (gender IN ('male','female','other')),          -- Q19
  date_of_birth         date NOT NULL CHECK (date_of_birth > DATE '1900-01-01'),             -- needed to know minors (Q44)
  photo_file_id         uuid NOT NULL,                                                         -- mandatory (Q39)
  admission_date        date NOT NULL,
  institution_roll_no   text,
  program               text,
  year_of_study         smallint CHECK (year_of_study BETWEEN 1 AND 10),
  address_line1         text,
  city                  text,
  state                 text,
  postal_code           text CHECK (postal_code ~ '^[0-9]{6}$'),
  status                text NOT NULL DEFAULT 'active' CHECK (status IN ('active','exited')),
  exited_on             date,
  exit_reason           text CHECK (exit_reason IN ('graduated','withdrawn','expelled','transferred_out','other')),
  created_by            uuid NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, student_code),
  UNIQUE (org_id, user_id),
  FOREIGN KEY (org_id, user_id)              REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, hostel_id)            REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, admission_session_id) REFERENCES academic_sessions (org_id, id),
  FOREIGN KEY (org_id, photo_file_id)        REFERENCES files (org_id, id),
  FOREIGN KEY (org_id, created_by)           REFERENCES users (org_id, id),
  CHECK ((status = 'exited') = (exited_on IS NOT NULL)),
  CHECK ((exited_on IS NULL) = (exit_reason IS NULL))
);
CREATE INDEX ON students (org_id, hostel_id, status);

CREATE TABLE student_guardians (                     -- emergency contacts only; guardians are not users (§4)
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id        uuid NOT NULL REFERENCES organizations (id),
  student_id    uuid NOT NULL,
  full_name     text NOT NULL CHECK (length(full_name) BETWEEN 1 AND 120),
  relationship  text NOT NULL CHECK (relationship IN ('father','mother','guardian','sibling','spouse','other')),
  phone         text NOT NULL CHECK (phone ~ '^\+?[0-9][0-9 -]{6,19}$'),
  alt_phone     text CHECK (alt_phone ~ '^\+?[0-9][0-9 -]{6,19}$'),
  email         text CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  is_primary    boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, student_id) REFERENCES students (org_id, id)
);
CREATE INDEX ON student_guardians (org_id, student_id);
CREATE UNIQUE INDEX student_guardians_one_primary_uq ON student_guardians (student_id) WHERE is_primary;

CREATE TABLE student_documents (
  id                  uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id              uuid NOT NULL REFERENCES organizations (id),
  student_id          uuid NOT NULL,
  file_id             uuid NOT NULL UNIQUE,
  file_category       text NOT NULL,              -- copied from files; the FK below guarantees it matches
  file_mime_type      text NOT NULL,              -- copied from files; the FK below guarantees it matches
  document_type       text NOT NULL CHECK (document_type IN ('identity','admission','other')),
  id_type             text CHECK (id_type IN ('aadhaar','pan','passport','voter_id','driving_licence','college_id','other')),
  id_number_masked    text CHECK (id_number_masked ~ '^X{4,16}[0-9A-Z]{4}$'),   -- only the last 4 characters (Q44, Q62)
  masked_attested_by  uuid,                       -- uploader confirms: the masked Aadhaar PDF downloaded from UIDAI (Q62)
  masked_attested_at  timestamptz,
  uploaded_by         uuid NOT NULL,
  verified_by         uuid,
  verified_at         timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, student_id) REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, file_id, file_category, file_mime_type)
    REFERENCES files (org_id, id, category, mime_type),
  FOREIGN KEY (org_id, uploaded_by)        REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, masked_attested_by) REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, verified_by)        REFERENCES users (org_id, id),
  -- identity documents always live in the restricted 'identity_document' category; others in 'admission_document'
  CHECK ((document_type = 'identity') = (file_category = 'identity_document')),
  CHECK (document_type = 'identity' OR file_category = 'admission_document'),
  CHECK ((document_type = 'identity') = (id_type IS NOT NULL)),
  CHECK (id_number_masked IS NULL OR id_type IS NOT NULL),
  -- Aadhaar: only the masked PDF from UIDAI, with the masked number and the uploader's attestation (Q62)
  CHECK (id_type IS DISTINCT FROM 'aadhaar'
         OR (file_mime_type = 'application/pdf' AND id_number_masked IS NOT NULL AND masked_attested_by IS NOT NULL)),
  CHECK ((masked_attested_by IS NULL) = (masked_attested_at IS NULL)),
  CHECK ((verified_by IS NULL) = (verified_at IS NULL))
);
CREATE INDEX ON student_documents (org_id, student_id);

-- ---------------------------------------------------------------- 5.7 Bed allocation
CREATE TABLE bed_allocations (
  id                      uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id                  uuid NOT NULL REFERENCES organizations (id),
  hostel_id               uuid NOT NULL,
  bed_id                  uuid NOT NULL,
  student_id              uuid NOT NULL,
  academic_session_id     uuid NOT NULL,
  status                  text NOT NULL CHECK (status IN ('reserved','active','ended','cancelled')),
  start_date              date NOT NULL,          -- planned check-in (reserved) or actual check-in date
  end_date                date,                   -- planned or actual check-out; NULL = open-ended
  stay                    daterange GENERATED ALWAYS AS (daterange(start_date, end_date, '[)')) STORED,
  reservation_expires_at  timestamptz,            -- Q21
  checked_in_at           timestamptz,
  checked_out_at          timestamptz,
  end_reason              text CHECK (end_reason IN ('checkout','transfer','exit','reservation_expired','cancelled')),
  previous_allocation_id  uuid,                   -- set on transfers
  created_by              uuid NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, hostel_id, bed_id)       REFERENCES beds (org_id, hostel_id, id),
  FOREIGN KEY (org_id, student_id)              REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, academic_session_id)     REFERENCES academic_sessions (org_id, id),
  FOREIGN KEY (org_id, previous_allocation_id)  REFERENCES bed_allocations (org_id, id),
  FOREIGN KEY (org_id, created_by)              REFERENCES users (org_id, id),
  CHECK (end_date IS NULL OR end_date > start_date),
  CHECK (status <> 'reserved' OR reservation_expires_at IS NOT NULL),
  CHECK (status NOT IN ('active','ended') OR checked_in_at IS NOT NULL),
  CHECK ((status = 'ended') = (checked_out_at IS NOT NULL)),
  CHECK (status <> 'ended' OR end_date IS NOT NULL),
  CHECK ((status IN ('ended','cancelled')) = (end_reason IS NOT NULL)),
  -- THE double-booking guarantee (ALC-5): no two live stays may overlap, for a bed or for a student
  CONSTRAINT bed_no_overlap     EXCLUDE USING gist (bed_id WITH =, stay WITH &&)
                                WHERE (status IN ('reserved','active','ended')),
  CONSTRAINT student_no_overlap EXCLUDE USING gist (student_id WITH =, stay WITH &&)
                                WHERE (status IN ('reserved','active','ended'))
);
CREATE UNIQUE INDEX bed_allocations_one_successor_uq ON bed_allocations (previous_allocation_id)
  WHERE previous_allocation_id IS NOT NULL;
CREATE INDEX ON bed_allocations (org_id, student_id, start_date DESC);
CREATE INDEX ON bed_allocations (org_id, hostel_id, status);
CREATE INDEX ON bed_allocations (reservation_expires_at) WHERE status = 'reserved';

-- ---------------------------------------------------------------- 5.8 Fees, payments and the ledger
CREATE TABLE fee_structures (
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id               uuid NOT NULL REFERENCES organizations (id),
  academic_session_id  uuid NOT NULL,
  hostel_id            uuid,                     -- NULL = all hostels
  room_type_id         uuid,                     -- NULL = all room types (Q20)
  name                 text NOT NULL CHECK (length(name) BETWEEN 2 AND 80),
  fee_kind             text NOT NULL CHECK (fee_kind IN ('rent','deposit','other')),
  amount               numeric(12,2) NOT NULL CHECK (amount > 0),
  billing_cycle        text NOT NULL CHECK (billing_cycle IN ('one_time','monthly','term','annual')),   -- Q24
  due_days             smallint NOT NULL DEFAULT 10 CHECK (due_days BETWEEN 0 AND 90),     -- due N days after period start
  grace_days           smallint NOT NULL DEFAULT 0  CHECK (grace_days BETWEEN 0 AND 90),   -- Q26
  late_fee_type        text NOT NULL DEFAULT 'none' CHECK (late_fee_type IN ('none','flat','per_day')),
  late_fee_amount      numeric(12,2) CHECK (late_fee_amount > 0),
  late_fee_cap         numeric(12,2) CHECK (late_fee_cap > 0),
  is_active            boolean NOT NULL DEFAULT true,
  created_by           uuid NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, academic_session_id)        REFERENCES academic_sessions (org_id, id),
  FOREIGN KEY (org_id, hostel_id)                  REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, hostel_id, room_type_id)    REFERENCES room_types (org_id, hostel_id, id),
  FOREIGN KEY (org_id, created_by)                 REFERENCES users (org_id, id),
  CHECK (room_type_id IS NULL OR hostel_id IS NOT NULL),
  CHECK ((late_fee_type = 'none') = (late_fee_amount IS NULL)),
  CHECK ((late_fee_type = 'per_day') = (late_fee_cap IS NOT NULL)),
  CHECK (fee_kind <> 'deposit' OR (billing_cycle = 'one_time' AND late_fee_type = 'none'))
);
CREATE INDEX ON fee_structures (org_id, academic_session_id, hostel_id) WHERE is_active;

CREATE TABLE payment_submissions (                 -- student-reported UPI payments (Q28)
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id         uuid NOT NULL REFERENCES organizations (id),
  hostel_id      uuid NOT NULL,
  student_id     uuid NOT NULL,
  amount         numeric(12,2) NOT NULL CHECK (amount > 0),
  utr            text NOT NULL CHECK (utr ~ '^[A-Za-z0-9]{6,35}$'),
  paid_on        date NOT NULL,
  proof_file_id  uuid UNIQUE,
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','verified','rejected')),
  submitted_by   uuid NOT NULL,                    -- the student's user
  submitted_at   timestamptz NOT NULL DEFAULT now(),
  reviewed_by    uuid,
  reviewed_at    timestamptz,
  review_note    text,
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, hostel_id)     REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, student_id)    REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, proof_file_id) REFERENCES files (org_id, id),
  FOREIGN KEY (org_id, submitted_by)  REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, reviewed_by)   REFERENCES users (org_id, id),
  CHECK ((status = 'pending') = (reviewed_by IS NULL)),
  CHECK ((reviewed_by IS NULL) = (reviewed_at IS NULL)),
  CHECK (status <> 'rejected' OR review_note IS NOT NULL),
  CHECK (reviewed_by IS NULL OR reviewed_by <> submitted_by)
);
CREATE UNIQUE INDEX payment_submissions_utr_uq ON payment_submissions (org_id, utr) WHERE status <> 'rejected';
CREATE INDEX ON payment_submissions (org_id, status, submitted_at);
CREATE INDEX ON payment_submissions (org_id, student_id);

CREATE TABLE payments (                            -- append-only; one receipt each
  id               uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id           uuid NOT NULL REFERENCES organizations (id),
  hostel_id        uuid NOT NULL,
  student_id       uuid NOT NULL,
  amount           numeric(12,2) NOT NULL CHECK (amount > 0),
  method           text NOT NULL CHECK (method IN ('cash','upi','bank_transfer')),
  reference        text CHECK (reference ~ '^[A-Za-z0-9/-]{4,40}$'),       -- UTR / bank reference
  received_on      date NOT NULL,
  financial_year   text NOT NULL CHECK (financial_year ~ '^[0-9]{4}-[0-9]{2}$'),   -- '2026-27' (April–March)
  receipt_seq      integer NOT NULL CHECK (receipt_seq BETWEEN 1 AND 999999),
  receipt_number   text GENERATED ALWAYS AS
                     ('RCPT/' || financial_year || '/' || lpad(receipt_seq::text, 6, '0')) STORED,   -- Q29
  idempotency_key  uuid NOT NULL,
  submission_id    uuid UNIQUE,
  recorded_by      uuid NOT NULL,
  notes            text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, financial_year, receipt_seq),
  UNIQUE (org_id, idempotency_key),
  FOREIGN KEY (org_id, hostel_id)     REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, student_id)    REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, submission_id) REFERENCES payment_submissions (org_id, id),
  FOREIGN KEY (org_id, recorded_by)   REFERENCES users (org_id, id),
  CHECK (method = 'cash' OR reference IS NOT NULL),
  CHECK (submission_id IS NULL OR method = 'upi')
);
CREATE UNIQUE INDEX payments_reference_uq ON payments (org_id, method, reference) WHERE reference IS NOT NULL;
CREATE INDEX ON payments (org_id, student_id, received_on DESC);
CREATE INDEX ON payments (org_id, received_on);

CREATE TABLE payment_reversals (                   -- append-only; a payment is reversed at most once (Q31)
  payment_id   uuid PRIMARY KEY,
  org_id       uuid NOT NULL REFERENCES organizations (id),
  reason       text NOT NULL CHECK (length(reason) BETWEEN 5 AND 500),
  reversed_by  uuid NOT NULL,
  reversed_at  timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, payment_id)  REFERENCES payments (org_id, id),
  FOREIGN KEY (org_id, reversed_by) REFERENCES users (org_id, id)
);

CREATE TABLE refund_requests (                     -- maker-checker workflow (Q30, Q59)
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id          uuid NOT NULL REFERENCES organizations (id),
  hostel_id       uuid NOT NULL,
  student_id      uuid NOT NULL,
  account         text NOT NULL CHECK (account IN ('dues','deposit')),
  amount          numeric(12,2) NOT NULL CHECK (amount > 0),
  reason          text NOT NULL CHECK (length(reason) BETWEEN 5 AND 500),
  status          text NOT NULL DEFAULT 'requested'
                  CHECK (status IN ('requested','approved','rejected','paid','cancelled')),
  requested_by    uuid NOT NULL,
  requested_at    timestamptz NOT NULL DEFAULT now(),
  decided_by      uuid,
  decided_at      timestamptz,
  decision_note   text,
  paid_method     text CHECK (paid_method IN ('cash','upi','bank_transfer')),
  paid_reference  text,
  paid_at         timestamptz,
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, hostel_id)    REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, student_id)   REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, requested_by) REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, decided_by)   REFERENCES users (org_id, id),
  CHECK (decided_by IS NULL OR decided_by <> requested_by),           -- a different person, even a Super Admin
  CHECK ((status IN ('approved','rejected','paid')) = (decided_by IS NOT NULL)),
  CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
  CHECK (status <> 'rejected' OR decision_note IS NOT NULL),
  CHECK ((status = 'paid') = (paid_at IS NOT NULL)),
  CHECK ((paid_at IS NULL) = (paid_method IS NULL)),
  CHECK (paid_method IS NULL OR paid_method = 'cash' OR paid_reference IS NOT NULL)
);
CREATE INDEX ON refund_requests (org_id, status, requested_at);
CREATE INDEX ON refund_requests (org_id, student_id);

CREATE TABLE ledger_entries (                      -- append-only; THE source of truth for balances
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id               uuid NOT NULL REFERENCES organizations (id),
  hostel_id            uuid NOT NULL,              -- the student's hostel when the entry was made
  student_id           uuid NOT NULL,
  txn_id               uuid NOT NULL,              -- groups the entries of one business action
  account              text NOT NULL CHECK (account IN ('dues','deposit')),
  entry_type           text NOT NULL CHECK (entry_type IN ('charge','late_fee','fine','waiver','payment','refund',
                         'deposit_received','deposit_refunded','deposit_applied','reversal')),
  amount               numeric(12,2) NOT NULL CHECK (amount <> 0),   -- signed: + increases the balance
  effective_date       date NOT NULL,
  description          text NOT NULL CHECK (length(description) BETWEEN 2 AND 200),
  fee_structure_id     uuid,
  academic_session_id  uuid,
  period_start         date,
  period_end           date,
  due_date             date,
  source_entry_id      uuid,                       -- late fee → the overdue charge
  late_fee_date        date,
  payment_id           uuid,
  refund_request_id    uuid,
  reverses_entry_id    uuid UNIQUE,                -- each entry can be reversed at most once
  reason_code          text CHECK (reason_code IN ('waiver','discount','scholarship','proration','damage','correction','other')),
  reason_note          text,
  created_by           uuid,                       -- NULL only for system-generated charges and late fees
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, student_id, id),               -- lets ledger_allocations require both sides to be one student's
  FOREIGN KEY (org_id, hostel_id)            REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, student_id)           REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, fee_structure_id)     REFERENCES fee_structures (org_id, id),
  FOREIGN KEY (org_id, academic_session_id)  REFERENCES academic_sessions (org_id, id),
  FOREIGN KEY (org_id, source_entry_id)      REFERENCES ledger_entries (org_id, id),
  FOREIGN KEY (org_id, payment_id)           REFERENCES payments (org_id, id),
  FOREIGN KEY (org_id, refund_request_id)    REFERENCES refund_requests (org_id, id),
  FOREIGN KEY (org_id, reverses_entry_id)    REFERENCES ledger_entries (org_id, id),
  FOREIGN KEY (org_id, created_by)           REFERENCES users (org_id, id),
  -- Sign and account per entry type (one place to read all of them)
  CONSTRAINT ledger_sign_rules CHECK (
       (entry_type IN ('charge','late_fee','fine','refund') AND account = 'dues'    AND amount > 0)
    OR (entry_type IN ('waiver','payment')                  AND account = 'dues'    AND amount < 0)
    OR (entry_type =  'deposit_received'                    AND account = 'deposit' AND amount > 0)
    OR (entry_type =  'deposit_refunded'                    AND account = 'deposit' AND amount < 0)
    OR (entry_type =  'deposit_applied'                                             AND amount < 0)
    OR (entry_type =  'reversal')
  ),
  CHECK ((entry_type = 'reversal') = (reverses_entry_id IS NOT NULL)),
  CHECK (entry_type <> 'reversal' OR reason_note IS NOT NULL),
  CHECK ((entry_type IN ('payment','deposit_received')) = (payment_id IS NOT NULL)),
  CHECK ((entry_type IN ('refund','deposit_refunded')) = (refund_request_id IS NOT NULL)),
  CHECK ((entry_type = 'late_fee') = (source_entry_id IS NOT NULL)),
  CHECK ((entry_type = 'late_fee') = (late_fee_date IS NOT NULL)),
  CHECK (entry_type NOT IN ('charge','late_fee','fine') OR due_date IS NOT NULL),
  CHECK (entry_type <> 'charge' OR academic_session_id IS NOT NULL),
  CHECK (entry_type NOT IN ('waiver','fine') OR (reason_code IS NOT NULL AND reason_note IS NOT NULL)),  -- Q27
  CHECK (period_end IS NULL OR (period_start IS NOT NULL AND period_end >= period_start)),
  CHECK (created_by IS NOT NULL OR entry_type IN ('charge','late_fee'))
);
CREATE INDEX ON ledger_entries (org_id, student_id, account, effective_date);
CREATE INDEX ON ledger_entries (org_id, hostel_id, effective_date);
CREATE INDEX ON ledger_entries (org_id, entry_type, effective_date);
CREATE INDEX ON ledger_entries (payment_id) WHERE payment_id IS NOT NULL;
CREATE INDEX ON ledger_entries (txn_id);
-- Re-running the charge generator or the late-fee job can't double-charge (Q26)
CREATE UNIQUE INDEX ledger_one_charge_per_period_uq ON ledger_entries (student_id, fee_structure_id, period_start)
  WHERE entry_type = 'charge' AND fee_structure_id IS NOT NULL;
CREATE UNIQUE INDEX ledger_one_late_fee_per_day_uq ON ledger_entries (source_entry_id, late_fee_date)
  WHERE entry_type = 'late_fee';
CREATE UNIQUE INDEX ledger_one_refund_per_request_uq ON ledger_entries (refund_request_id)
  WHERE refund_request_id IS NOT NULL;

CREATE TABLE ledger_allocations (                  -- which credit settled which charge (Q60)
  id               uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id           uuid NOT NULL REFERENCES organizations (id),
  student_id       uuid NOT NULL,
  credit_entry_id  uuid NOT NULL,                  -- dues entry with amount < 0: payment, waiver, deposit_applied
  debit_entry_id   uuid NOT NULL,                  -- dues entry with amount > 0: charge, late_fee, fine, refund
  amount           numeric(12,2) NOT NULL CHECK (amount > 0),
  method           text NOT NULL CHECK (method IN ('auto_oldest_first','manual')),
  allocated_by     uuid,                           -- NULL for automatic allocation
  allocated_at     timestamptz NOT NULL DEFAULT now(),
  released_at      timestamptz,                    -- set once, e.g. when either side is reversed
  released_by      uuid,
  release_reason   text CHECK (release_reason IN ('credit_reversed','debit_reversed','reallocated')),
  FOREIGN KEY (org_id, student_id)                  REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, student_id, credit_entry_id) REFERENCES ledger_entries (org_id, student_id, id),
  FOREIGN KEY (org_id, student_id, debit_entry_id)  REFERENCES ledger_entries (org_id, student_id, id),
  FOREIGN KEY (org_id, allocated_by)                REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, released_by)                 REFERENCES users (org_id, id),
  CHECK (credit_entry_id <> debit_entry_id),
  CHECK ((method = 'manual') = (allocated_by IS NOT NULL)),
  CHECK ((released_at IS NULL) = (release_reason IS NULL)),
  CHECK (released_by IS NULL OR released_at IS NOT NULL)
);
CREATE UNIQUE INDEX ledger_allocations_active_pair_uq ON ledger_allocations (credit_entry_id, debit_entry_id)
  WHERE released_at IS NULL;
CREATE INDEX ON ledger_allocations (debit_entry_id)  WHERE released_at IS NULL;
CREATE INDEX ON ledger_allocations (credit_entry_id) WHERE released_at IS NULL;
CREATE INDEX ON ledger_allocations (org_id, student_id, allocated_at);

-- ---------------------------------------------------------------- 5.9 Attendance and QR redemption
CREATE TABLE attendance_sessions (                 -- one per hostel per day (Q32)
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id        uuid NOT NULL REFERENCES organizations (id),
  hostel_id     uuid NOT NULL,
  session_date  date NOT NULL,                     -- the hostel-local date
  opens_at      timestamptz NOT NULL,
  closes_at     timestamptz NOT NULL,
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  closed_at     timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostel_id, id),
  UNIQUE (org_id, hostel_id, session_date),
  FOREIGN KEY (org_id, hostel_id) REFERENCES hostels (org_id, id),
  CHECK (closes_at > opens_at),
  CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);

CREATE TABLE attendance_records (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id             uuid NOT NULL REFERENCES organizations (id),
  hostel_id          uuid NOT NULL,
  session_id         uuid NOT NULL,
  student_id         uuid NOT NULL,
  status             text NOT NULL CHECK (status IN ('present','absent','on_leave')),
  method             text NOT NULL CHECK (method IN ('qr','manual','system')),   -- Q33
  recorded_by        uuid,                          -- NULL only for 'system' (auto-marking at close)
  recorded_at        timestamptz NOT NULL DEFAULT now(),
  corrected_by       uuid,
  corrected_at       timestamptz,
  correction_reason  text,
  UNIQUE (session_id, student_id),                  -- duplicates impossible (ATT-2)
  FOREIGN KEY (org_id, hostel_id, session_id) REFERENCES attendance_sessions (org_id, hostel_id, id),
  FOREIGN KEY (org_id, student_id)            REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, recorded_by)           REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, corrected_by)          REFERENCES users (org_id, id),
  CHECK ((method = 'system') = (recorded_by IS NULL)),
  CHECK (method <> 'qr' OR status = 'present' OR corrected_at IS NOT NULL),
  CHECK ((corrected_at IS NULL) = (corrected_by IS NULL)),
  CHECK (corrected_at IS NULL OR correction_reason IS NOT NULL)
);
CREATE INDEX ON attendance_records (org_id, student_id, recorded_at DESC);

CREATE TABLE qr_redemptions (                      -- each personal QR token works once (PROPOSAL; Q57)
  jti          uuid PRIMARY KEY,                   -- the token's one-time ID
  org_id       uuid NOT NULL REFERENCES organizations (id),
  student_id   uuid NOT NULL,
  purpose      text NOT NULL CHECK (purpose IN ('attendance','gate')),
  redeemed_by  uuid NOT NULL,
  redeemed_at  timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, student_id)  REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, redeemed_by) REFERENCES users (org_id, id)
);
CREATE INDEX ON qr_redemptions (redeemed_at);       -- purge after 2 days

-- ---------------------------------------------------------------- 5.10 Leave and gate
CREATE TABLE leave_requests (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id         uuid NOT NULL REFERENCES organizations (id),
  hostel_id      uuid NOT NULL,
  student_id     uuid NOT NULL,
  leave_type     text NOT NULL CHECK (leave_type IN ('home','local_outing','emergency')),   -- Q35
  reason         text NOT NULL CHECK (length(reason) BETWEEN 3 AND 500),
  destination    text,
  starts_at      timestamptz NOT NULL,
  ends_at        timestamptz NOT NULL,
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','cancelled')),
  requested_by   uuid NOT NULL,                     -- the student, or the warden for emergency leave
  decided_by     uuid,
  decided_at     timestamptz,
  auto_approved  boolean NOT NULL DEFAULT false,    -- local outings where the hostel needs no approval
  decision_note  text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, hostel_id, student_id, id),
  FOREIGN KEY (org_id, hostel_id)    REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, student_id)   REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, requested_by) REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, decided_by)   REFERENCES users (org_id, id),
  CHECK (ends_at > starts_at),
  CHECK (NOT auto_approved OR leave_type = 'local_outing'),
  CHECK (status NOT IN ('approved','rejected') OR decided_by IS NOT NULL OR auto_approved),
  CHECK (decided_by IS NULL OR decided_at IS NOT NULL),
  CHECK (status <> 'rejected' OR decision_note IS NOT NULL),
  CONSTRAINT leave_no_overlap EXCLUDE USING gist
    (student_id WITH =, (tstzrange(starts_at, ends_at)) WITH &&) WHERE (status IN ('pending','approved'))
);
CREATE INDEX ON leave_requests (org_id, hostel_id, status, starts_at);
CREATE INDEX ON leave_requests (org_id, student_id, starts_at DESC);

CREATE TABLE gate_passes (                         -- no QR token of its own (Q58)
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id               uuid NOT NULL REFERENCES organizations (id),
  hostel_id            uuid NOT NULL,
  student_id           uuid NOT NULL,
  leave_request_id     uuid NOT NULL UNIQUE,      -- one pass per leave
  exit_allowed_from    timestamptz NOT NULL,      -- leave start − hostel exit buffer (Q37)
  leave_ends_at        timestamptz NOT NULL,
  return_due_by        timestamptz NOT NULL,      -- leave end + hostel return grace (Q37)
  status               text NOT NULL DEFAULT 'approved'
                       CHECK (status IN ('approved','out','returned','expired','cancelled')),
  exited_at            timestamptz,
  returned_at          timestamptz,
  late_return          boolean GENERATED ALWAYS AS (returned_at > return_due_by) STORED,
  overdue_notified_at  timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  -- the pass must match its leave's student and hostel
  FOREIGN KEY (org_id, hostel_id, student_id, leave_request_id)
    REFERENCES leave_requests (org_id, hostel_id, student_id, id),
  CHECK (exit_allowed_from < leave_ends_at AND leave_ends_at <= return_due_by),
  CHECK ((status IN ('out','returned')) = (exited_at IS NOT NULL)),
  CHECK ((status = 'returned') = (returned_at IS NOT NULL)),
  CHECK (returned_at IS NULL OR returned_at >= exited_at)
);
CREATE UNIQUE INDEX gate_passes_one_out_per_student_uq ON gate_passes (student_id) WHERE status = 'out';
CREATE INDEX ON gate_passes (org_id, student_id, status);
CREATE INDEX ON gate_passes (return_due_by) WHERE status = 'out';        -- overdue job
CREATE INDEX ON gate_passes (leave_ends_at) WHERE status = 'approved';    -- expiry job

CREATE TABLE gate_events (                         -- append-only, except the warden's acknowledgement
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id            uuid NOT NULL REFERENCES organizations (id),
  hostel_id         uuid NOT NULL,
  student_id        uuid NOT NULL,
  gate_pass_id      uuid,                          -- NULL for overrides without a valid pass
  direction         text NOT NULL CHECK (direction IN ('exit','return')),
  method            text NOT NULL CHECK (method IN ('qr','override')),
  occurred_at       timestamptz NOT NULL DEFAULT now(),
  recorded_by       uuid NOT NULL,                 -- the guard
  override_reason   text CHECK (override_reason IN ('no_phone','network_down','emergency','no_valid_pass','other')),
  override_note     text,
  acknowledged_by   uuid,                          -- the warden (Q38)
  acknowledged_at   timestamptz,
  request_id        uuid,
  FOREIGN KEY (org_id, hostel_id)       REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, student_id)      REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, gate_pass_id)    REFERENCES gate_passes (org_id, id),
  FOREIGN KEY (org_id, recorded_by)     REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, acknowledged_by) REFERENCES users (org_id, id),
  CHECK (method <> 'qr' OR gate_pass_id IS NOT NULL),
  CHECK ((method = 'override') = (override_reason IS NOT NULL)),
  CHECK (override_reason <> 'other' OR override_note IS NOT NULL),
  CHECK ((acknowledged_by IS NULL) = (acknowledged_at IS NULL)),
  CHECK (acknowledged_by IS NULL OR (method = 'override' AND acknowledged_by <> recorded_by))
);
CREATE INDEX ON gate_events (org_id, hostel_id, occurred_at DESC);
CREATE INDEX ON gate_events (org_id, student_id, occurred_at DESC);
CREATE INDEX gate_events_unacknowledged_idx ON gate_events (org_id, hostel_id)
  WHERE method = 'override' AND acknowledged_at IS NULL;

-- ---------------------------------------------------------------- 5.11 Complaints
CREATE TABLE complaint_categories (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id      uuid NOT NULL REFERENCES organizations (id),
  name        text NOT NULL CHECK (length(name) BETWEEN 2 AND 60),
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  UNIQUE (org_id, name)
);

CREATE TABLE complaints (
  id               uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id           uuid NOT NULL REFERENCES organizations (id),
  hostel_id        uuid NOT NULL,
  student_id       uuid NOT NULL,                  -- never anonymous (Q40)
  room_id          uuid,
  category_id      uuid NOT NULL,
  title            text NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
  description      text NOT NULL CHECK (length(description) BETWEEN 3 AND 4000),
  priority         text NOT NULL CHECK (priority IN ('low','medium','high','urgent')),
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','resolved','closed')),
  target_date      date NOT NULL,                  -- derived from priority (Q40)
  assigned_to      uuid,
  resolved_at      timestamptz,
  resolved_by      uuid,
  resolution_note  text,
  closed_at        timestamptz,
  reopen_count     smallint NOT NULL DEFAULT 0 CHECK (reopen_count >= 0),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, hostel_id)          REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, student_id)         REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, hostel_id, room_id) REFERENCES rooms (org_id, hostel_id, id),
  FOREIGN KEY (org_id, category_id)        REFERENCES complaint_categories (org_id, id),
  FOREIGN KEY (org_id, assigned_to)        REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, resolved_by)        REFERENCES users (org_id, id),
  CHECK (status <> 'in_progress' OR assigned_to IS NOT NULL),
  CHECK ((status IN ('resolved','closed')) = (resolved_at IS NOT NULL)),
  CHECK (resolved_at IS NULL OR (resolved_by IS NOT NULL AND resolution_note IS NOT NULL)),
  CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);
CREATE INDEX ON complaints (org_id, hostel_id, status, priority);
CREATE INDEX ON complaints (org_id, student_id, created_at DESC);
CREATE INDEX ON complaints (org_id, assigned_to) WHERE status IN ('open','in_progress');

CREATE TABLE complaint_updates (                   -- append-only history and comments
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id        uuid NOT NULL REFERENCES organizations (id),
  complaint_id  uuid NOT NULL,
  author_id     uuid NOT NULL,
  from_status   text CHECK (from_status IN ('open','in_progress','resolved','closed')),
  to_status     text CHECK (to_status   IN ('open','in_progress','resolved','closed')),
  comment       text CHECK (length(comment) BETWEEN 1 AND 2000),
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, complaint_id) REFERENCES complaints (org_id, id),
  FOREIGN KEY (org_id, author_id)    REFERENCES users (org_id, id),
  CHECK (to_status IS NOT NULL OR comment IS NOT NULL)
);
CREATE INDEX ON complaint_updates (org_id, complaint_id, created_at);

CREATE TABLE complaint_attachments (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id        uuid NOT NULL REFERENCES organizations (id),
  complaint_id  uuid NOT NULL,
  file_id       uuid NOT NULL UNIQUE,
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, complaint_id) REFERENCES complaints (org_id, id),
  FOREIGN KEY (org_id, file_id)      REFERENCES files (org_id, id)
);
CREATE INDEX ON complaint_attachments (org_id, complaint_id);

-- ---------------------------------------------------------------- 5.12 Mess
CREATE TABLE mess_menus (                          -- per hostel (Q42)
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id      uuid NOT NULL REFERENCES organizations (id),
  hostel_id   uuid NOT NULL,
  menu_date   date NOT NULL,
  meal        text NOT NULL CHECK (meal IN ('breakfast','lunch','snacks','dinner')),
  items       text NOT NULL CHECK (length(items) BETWEEN 1 AND 1000),
  notes       text,
  updated_by  uuid NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostel_id, menu_date, meal),
  FOREIGN KEY (org_id, hostel_id)  REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, updated_by) REFERENCES users (org_id, id)
);

-- ---------------------------------------------------------------- 5.13 Inventory and assets
CREATE TABLE inventory_items (                     -- per hostel only (Q43)
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id            uuid NOT NULL REFERENCES organizations (id),
  hostel_id         uuid NOT NULL,
  name              text NOT NULL CHECK (length(name) BETWEEN 2 AND 80),
  unit              text NOT NULL CHECK (unit IN ('piece','kg','litre','pack','box','metre','other')),
  reorder_level     numeric(12,3) NOT NULL DEFAULT 0 CHECK (reorder_level >= 0),
  quantity_on_hand  numeric(12,3) NOT NULL DEFAULT 0 CHECK (quantity_on_hand >= 0),   -- stock can't go negative
  is_active         boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostel_id, id),
  UNIQUE (org_id, hostel_id, name),
  FOREIGN KEY (org_id, hostel_id) REFERENCES hostels (org_id, id)
);
CREATE INDEX inventory_low_stock_idx ON inventory_items (org_id, hostel_id)
  WHERE is_active AND quantity_on_hand <= reorder_level;

CREATE TABLE stock_movements (                     -- append-only; quantity_on_hand follows it via trigger
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id         uuid NOT NULL REFERENCES organizations (id),
  hostel_id      uuid NOT NULL,
  item_id        uuid NOT NULL,
  movement_type  text NOT NULL CHECK (movement_type IN ('addition','deduction','adjustment')),
  quantity       numeric(12,3) NOT NULL CHECK (quantity <> 0),
  reason         text NOT NULL CHECK (length(reason) BETWEEN 3 AND 300),
  performed_by   uuid NOT NULL,
  occurred_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, hostel_id, item_id) REFERENCES inventory_items (org_id, hostel_id, id),
  FOREIGN KEY (org_id, performed_by)       REFERENCES users (org_id, id),
  CHECK ((movement_type = 'addition'  AND quantity > 0)
      OR (movement_type = 'deduction' AND quantity < 0)
      OR  movement_type = 'adjustment')
);
CREATE INDEX ON stock_movements (org_id, item_id, occurred_at DESC);

CREATE TABLE assets (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  version  integer NOT NULL DEFAULT 1 CHECK (version >= 1),   -- optimistic locking (Q70)
  org_id        uuid NOT NULL REFERENCES organizations (id),
  hostel_id     uuid NOT NULL,
  asset_tag     text NOT NULL CHECK (asset_tag ~ '^[A-Z0-9-]{2,30}$'),
  name          text NOT NULL CHECK (length(name) BETWEEN 2 AND 80),
  category      text NOT NULL CHECK (category IN ('furniture','electrical','electronics','kitchen','sports','other')),
  status        text NOT NULL DEFAULT 'in_store' CHECK (status IN ('in_store','in_use','under_repair','disposed')),
  purchased_on  date,
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, hostel_id, id),
  UNIQUE (org_id, asset_tag),
  FOREIGN KEY (org_id, hostel_id) REFERENCES hostels (org_id, id)
);
CREATE INDEX ON assets (org_id, hostel_id, status);

CREATE TABLE asset_assignments (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id       uuid NOT NULL REFERENCES organizations (id),
  hostel_id    uuid NOT NULL,
  asset_id     uuid NOT NULL,
  room_id      uuid,
  user_id      uuid,                               -- a staff member
  student_id   uuid,
  assigned_by  uuid NOT NULL,
  assigned_at  timestamptz NOT NULL DEFAULT now(),
  returned_at  timestamptz,
  return_note  text,
  FOREIGN KEY (org_id, hostel_id, asset_id) REFERENCES assets (org_id, hostel_id, id),
  FOREIGN KEY (org_id, hostel_id, room_id)  REFERENCES rooms (org_id, hostel_id, id),
  FOREIGN KEY (org_id, user_id)             REFERENCES users (org_id, id),
  FOREIGN KEY (org_id, student_id)          REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, assigned_by)         REFERENCES users (org_id, id),
  CHECK (num_nonnulls(room_id, user_id, student_id) = 1),     -- assigned to exactly one target
  CHECK (returned_at IS NULL OR returned_at >= assigned_at)
);
CREATE UNIQUE INDEX asset_assignments_one_active_uq ON asset_assignments (asset_id) WHERE returned_at IS NULL;
CREATE INDEX ON asset_assignments (org_id, hostel_id, assigned_at DESC);

-- ---------------------------------------------------------------- 5.14 Notifications and announcements
CREATE TABLE notifications (                       -- in-app
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id      uuid NOT NULL REFERENCES organizations (id),
  user_id     uuid NOT NULL,
  category    text NOT NULL CHECK (category IN ('security','fees','leave','attendance','complaints',
                                                  'announcements','allocation','admission','reminders')),
  title       text NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
  body        text NOT NULL CHECK (length(body) BETWEEN 1 AND 1000),
  link        text CHECK (link ~ '^/[A-Za-z0-9/_?=&.-]*$'),   -- relative links only: no external or javascript: URLs
  created_at  timestamptz NOT NULL DEFAULT now(),
  read_at     timestamptz,
  FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
);
CREATE INDEX notifications_unread_idx ON notifications (org_id, user_id, created_at DESC) WHERE read_at IS NULL;
CREATE INDEX ON notifications (org_id, user_id, created_at DESC);

CREATE TABLE notification_preferences (            -- Q49
  org_id         uuid NOT NULL REFERENCES organizations (id),
  user_id        uuid NOT NULL,
  category       text NOT NULL CHECK (category IN ('announcements','reminders')),   -- security emails can't be muted
  email_enabled  boolean NOT NULL,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, user_id, category),
  FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
);

CREATE TABLE announcements (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id        uuid NOT NULL REFERENCES organizations (id),
  hostel_id     uuid,                              -- NULL = whole organization
  title         text NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
  body          text NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
  audience      text NOT NULL CHECK (audience IN ('students','staff','everyone')),
  published_at  timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz,
  created_by    uuid NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, hostel_id)  REFERENCES hostels (org_id, id),
  FOREIGN KEY (org_id, created_by) REFERENCES users (org_id, id),
  CHECK (expires_at IS NULL OR expires_at > published_at)
);
CREATE INDEX ON announcements (org_id, hostel_id, published_at DESC);

-- ---------------------------------------------------------------- 5.15 Jobs
CREATE TABLE jobs (                                -- Q54
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id        uuid REFERENCES organizations (id),     -- NULL = platform-wide job (e.g. backup checks)
  kind          text NOT NULL CHECK (kind ~ '^[a-z_]+\.[a-z_]+$'),   -- e.g. 'email.send', 'fees.apply_late_fees'
  priority      smallint NOT NULL DEFAULT 1 CHECK (priority BETWEEN 0 AND 2),   -- 0 security, 1 transactional, 2 bulk
  payload       jsonb NOT NULL DEFAULT '{}',            -- IDs only; never personal data or secrets
  status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','dead')),
  run_at        timestamptz NOT NULL DEFAULT now(),
  attempts      smallint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts  smallint NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
  locked_by     text,
  locked_at     timestamptz,
  last_error    text,
  dedupe_key    text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  CHECK (attempts <= max_attempts),
  CHECK ((status = 'running') = (locked_at IS NOT NULL)),
  CHECK ((status IN ('succeeded','dead')) = (finished_at IS NOT NULL))
);
CREATE INDEX jobs_ready_idx ON jobs (priority, run_at) WHERE status = 'queued';
CREATE UNIQUE INDEX jobs_dedupe_uq ON jobs (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running');
CREATE INDEX ON jobs (finished_at) WHERE finished_at IS NOT NULL;      -- purge

-- ---------------------------------------------------------------- 5.16 Audit log
CREATE TABLE audit_logs (                          -- append-only (§9 of the brief)
  id                      uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id                  uuid REFERENCES organizations (id),   -- NULL = platform-only event
  hostel_id               uuid,
  occurred_at             timestamptz NOT NULL DEFAULT now(),
  actor_type              text NOT NULL CHECK (actor_type IN ('user','platform_user','system')),
  actor_user_id           uuid,
  actor_platform_user_id  uuid,
  actor_roles             text[] NOT NULL DEFAULT '{}',          -- roles at the time of the action
  action                  text NOT NULL CHECK (action ~ '^[a-z_]+\.[a-z_]+$'),   -- e.g. 'allocation.transferred'
  entity_type             text NOT NULL CHECK (entity_type ~ '^[a-z_]+$'),
  entity_id               uuid,
  changed_fields          text[] NOT NULL DEFAULT '{}',          -- names only; visible to the Platform Owner
  before_values           jsonb,                                  -- masked from the Platform Owner (Q55)
  after_values            jsonb,                                  -- masked from the Platform Owner (Q55)
  request_id              uuid,
  ip_address              inet,                                   -- masked from the Platform Owner (Q55)
  user_agent              text,                                   -- masked from the Platform Owner (Q55)
  CHECK ((actor_type = 'user') = (actor_user_id IS NOT NULL)),
  CHECK ((actor_type = 'platform_user') = (actor_platform_user_id IS NOT NULL)),
  CHECK (actor_type <> 'user' OR org_id IS NOT NULL)
);
CREATE INDEX ON audit_logs (org_id, occurred_at DESC);
CREATE INDEX ON audit_logs (org_id, entity_type, entity_id, occurred_at DESC);
CREATE INDEX ON audit_logs (org_id, actor_user_id, occurred_at DESC);
