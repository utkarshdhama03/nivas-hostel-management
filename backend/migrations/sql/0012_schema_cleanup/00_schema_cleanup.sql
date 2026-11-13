-- =============================================================================
-- 0012_schema_cleanup: drop what D75/D76 left unused (approved on 11 November).
--
--   Gate passes:  gate_events (its triggers and gate_event_ack_once()), gate_passes,
--                 hostels.gate_exit_buffer, hostels.gate_return_grace
--   Attendance:   attendance_records, attendance_sessions, hostels.attendance_window_start,
--                 hostels.attendance_window_length, the 'attendance' notification category
--   Personal QR:  qr_redemptions (and its triggers)
--   Old roles:    the revoked hostel_manager/warden/accountant/security_guard grants (the audit log
--                 keeps every grant and revocation), role_assignments.hostel_id; the role CHECK
--                 becomes role = 'super_admin'
--   Also:         hostels.curfew_time (nothing used it once gate passes went)
--
-- Dropping a table drops its indexes, RLS policy, triggers and grants with it. The downgrade
-- (down/00_restore.sql) recreates the structure exactly; the deleted rows don't come back.
-- Rows are deleted per organization: RLS is FORCEd, even for the owner running this.
-- =============================================================================

DO $$
DECLARE org record;
BEGIN
  FOR org IN SELECT id FROM organizations ORDER BY id LOOP
    PERFORM set_config('app.org_id', org.id::text, true);
    DELETE FROM role_assignments WHERE role <> 'super_admin';      -- revoked history rows only (D75)
    DELETE FROM notifications WHERE category = 'attendance';
  END LOOP;
  PERFORM set_config('app.org_id', '', true);
END $$;

-- --- gate passes --------------------------------------------------------------------------
DROP TABLE gate_events;
DROP FUNCTION gate_event_ack_once();
DROP TABLE gate_passes;
ALTER TABLE hostels DROP COLUMN gate_exit_buffer, DROP COLUMN gate_return_grace, DROP COLUMN curfew_time;

-- --- attendance and the personal QR -------------------------------------------------------
DROP TABLE attendance_records;
DROP TABLE attendance_sessions;
DROP TABLE qr_redemptions;
ALTER TABLE hostels DROP COLUMN attendance_window_start, DROP COLUMN attendance_window_length;
ALTER TABLE notifications DROP CONSTRAINT notifications_category_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_category_check CHECK (category IN
  ('security','fees','leave','complaints','announcements','allocation','admission','reminders'));

-- --- old roles ----------------------------------------------------------------------------
-- Dropping hostel_id drops what involves it: its foreign key, "Super Admins have no hostel",
-- role_assignments_active_uq and the (org_id, hostel_id, role) index. One active grant per person:
ALTER TABLE role_assignments DROP COLUMN hostel_id;
ALTER TABLE role_assignments DROP CONSTRAINT role_assignments_only_administrator;
ALTER TABLE role_assignments DROP CONSTRAINT role_assignments_role_check;
ALTER TABLE role_assignments ADD CONSTRAINT role_assignments_role_check CHECK (role = 'super_admin');
CREATE UNIQUE INDEX role_assignments_active_uq ON role_assignments (org_id, user_id, role) WHERE revoked_at IS NULL;
