-- =============================================================================
-- Downgrade of 0012_schema_cleanup: the dropped structure, exactly as migrations 0001-0011 left it
-- (business-clock defaults from 0005, the role CHECKs from 0008). Deleted rows don't come back.
-- =============================================================================

-- --- old roles ----------------------------------------------------------------------------
DROP INDEX role_assignments_active_uq;
ALTER TABLE role_assignments DROP CONSTRAINT role_assignments_role_check;
ALTER TABLE role_assignments ADD CONSTRAINT role_assignments_role_check
  CHECK (role IN ('super_admin','hostel_manager','warden','accountant','security_guard'));
ALTER TABLE role_assignments ADD CONSTRAINT role_assignments_only_administrator
  CHECK (role = 'super_admin' OR revoked_at IS NOT NULL);
ALTER TABLE role_assignments ADD COLUMN hostel_id uuid;   -- NULL = organization-wide (Q11)
ALTER TABLE role_assignments ADD CONSTRAINT role_assignments_org_id_hostel_id_fkey
  FOREIGN KEY (org_id, hostel_id) REFERENCES hostels (org_id, id);
ALTER TABLE role_assignments ADD CONSTRAINT role_assignments_check CHECK (role <> 'super_admin' OR hostel_id IS NULL);
CREATE UNIQUE INDEX role_assignments_active_uq
  ON role_assignments (org_id, user_id, role, hostel_id) NULLS NOT DISTINCT
  WHERE revoked_at IS NULL;
CREATE INDEX ON role_assignments (org_id, hostel_id, role) WHERE revoked_at IS NULL;

-- --- hostel settings and the notification category ----------------------------------------
ALTER TABLE hostels
  ADD COLUMN curfew_time time,
  ADD COLUMN attendance_window_start time NOT NULL DEFAULT '21:00',
  ADD COLUMN attendance_window_length interval NOT NULL DEFAULT '2 hours'
    CHECK (attendance_window_length > interval '0' AND attendance_window_length <= interval '12 hours'),
  ADD COLUMN gate_exit_buffer interval NOT NULL DEFAULT '2 hours' CHECK (gate_exit_buffer >= interval '0'),
  ADD COLUMN gate_return_grace interval NOT NULL DEFAULT '1 hour' CHECK (gate_return_grace >= interval '0');
ALTER TABLE notifications DROP CONSTRAINT notifications_category_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_category_check CHECK (category IN
  ('security','fees','leave','attendance','complaints','announcements','allocation','admission','reminders'));

-- --- attendance and the personal QR -------------------------------------------------------
CREATE TABLE attendance_sessions (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id        uuid NOT NULL REFERENCES organizations (id),
  hostel_id     uuid NOT NULL,
  session_date  date NOT NULL,
  opens_at      timestamptz NOT NULL,
  closes_at     timestamptz NOT NULL,
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  closed_at     timestamptz,
  created_at    timestamptz NOT NULL DEFAULT app_now(),
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
  method             text NOT NULL CHECK (method IN ('qr','manual','system')),
  recorded_by        uuid,
  recorded_at        timestamptz NOT NULL DEFAULT app_now(),
  corrected_by       uuid,
  corrected_at       timestamptz,
  correction_reason  text,
  UNIQUE (session_id, student_id),
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

CREATE TABLE qr_redemptions (
  jti          uuid PRIMARY KEY,
  org_id       uuid NOT NULL REFERENCES organizations (id),
  student_id   uuid NOT NULL,
  purpose      text NOT NULL CHECK (purpose IN ('attendance','gate')),
  redeemed_by  uuid NOT NULL,
  redeemed_at  timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, student_id)  REFERENCES students (org_id, id),
  FOREIGN KEY (org_id, redeemed_by) REFERENCES users (org_id, id)
);
CREATE INDEX ON qr_redemptions (redeemed_at);
CREATE TRIGGER qr_redemptions_no_update BEFORE UPDATE ON qr_redemptions
  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER qr_redemptions_no_truncate BEFORE TRUNCATE ON qr_redemptions
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- --- gate passes --------------------------------------------------------------------------
CREATE TABLE gate_passes (
  id                   uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id               uuid NOT NULL REFERENCES organizations (id),
  hostel_id            uuid NOT NULL,
  student_id           uuid NOT NULL,
  leave_request_id     uuid NOT NULL UNIQUE,
  exit_allowed_from    timestamptz NOT NULL,
  leave_ends_at        timestamptz NOT NULL,
  return_due_by        timestamptz NOT NULL,
  status               text NOT NULL DEFAULT 'approved'
                       CHECK (status IN ('approved','out','returned','expired','cancelled')),
  exited_at            timestamptz,
  returned_at          timestamptz,
  late_return          boolean GENERATED ALWAYS AS (returned_at > return_due_by) STORED,
  overdue_notified_at  timestamptz,
  created_at           timestamptz NOT NULL DEFAULT app_now(),
  updated_at           timestamptz NOT NULL DEFAULT app_now(),
  UNIQUE (org_id, id),
  FOREIGN KEY (org_id, hostel_id, student_id, leave_request_id)
    REFERENCES leave_requests (org_id, hostel_id, student_id, id),
  CHECK (exit_allowed_from < leave_ends_at AND leave_ends_at <= return_due_by),
  CHECK ((status IN ('out','returned')) = (exited_at IS NOT NULL)),
  CHECK ((status = 'returned') = (returned_at IS NOT NULL)),
  CHECK (returned_at IS NULL OR returned_at >= exited_at)
);
CREATE UNIQUE INDEX gate_passes_one_out_per_student_uq ON gate_passes (student_id) WHERE status = 'out';
CREATE INDEX ON gate_passes (org_id, student_id, status);
CREATE INDEX ON gate_passes (return_due_by) WHERE status = 'out';
CREATE INDEX ON gate_passes (leave_ends_at) WHERE status = 'approved';

CREATE TABLE gate_events (
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  org_id            uuid NOT NULL REFERENCES organizations (id),
  hostel_id         uuid NOT NULL,
  student_id        uuid NOT NULL,
  gate_pass_id      uuid,
  direction         text NOT NULL CHECK (direction IN ('exit','return')),
  method            text NOT NULL CHECK (method IN ('qr','override')),
  occurred_at       timestamptz NOT NULL DEFAULT app_now(),
  recorded_by       uuid NOT NULL,
  override_reason   text CHECK (override_reason IN ('no_phone','network_down','emergency','no_valid_pass','other')),
  override_note     text,
  acknowledged_by   uuid,
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
CREATE TRIGGER gate_events_no_delete BEFORE DELETE ON gate_events
  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER gate_events_no_truncate BEFORE TRUNCATE ON gate_events
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE FUNCTION gate_event_ack_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.acknowledged_at IS NOT NULL THEN
    RAISE EXCEPTION 'gate event % is already acknowledged', OLD.id
      USING ERRCODE = 'restrict_violation', CONSTRAINT = 'gate_events_ack_once';
  END IF;
  IF (to_jsonb(NEW) - 'acknowledged_by' - 'acknowledged_at')
     IS DISTINCT FROM (to_jsonb(OLD) - 'acknowledged_by' - 'acknowledged_at') THEN
    RAISE EXCEPTION 'only the acknowledgement of gate event % may change', OLD.id
      USING ERRCODE = 'restrict_violation', CONSTRAINT = 'gate_events_ack_once';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER gate_events_ack_once BEFORE UPDATE ON gate_events
  FOR EACH ROW EXECUTE FUNCTION gate_event_ack_once();

-- --- RLS and grants, as 40_rls.sql and 50_grants.sql gave them ----------------------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['attendance_sessions', 'attendance_records', 'qr_redemptions', 'gate_passes', 'gate_events']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I
                      USING (org_id = app_current_org())
                      WITH CHECK (org_id = app_current_org())', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON attendance_sessions, attendance_records, gate_passes TO hms_app, hms_worker;
GRANT SELECT, INSERT ON qr_redemptions, gate_events TO hms_app, hms_worker;
GRANT UPDATE (acknowledged_by, acknowledged_at) ON gate_events TO hms_app, hms_worker;
GRANT DELETE ON qr_redemptions TO hms_worker;
