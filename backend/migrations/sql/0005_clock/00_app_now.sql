-- =============================================================================
-- 0005_clock - an injectable clock for BUSINESS time only (D67).
--
-- app_now() is the time business logic runs at: the transaction-local setting
-- app.now when the application has set it, otherwise the real now(). The app
-- sets app.now from its own clock at the start of every transaction (app/core/db.py),
-- so in production app_now() = now(); tests freeze the application's clock and the
-- database follows.
--
-- THE RULE (03 §1): app_now() only for business tables and business
-- logic. Audit, login attempts, sessions, refresh/activation tokens, MFA, recovery
-- codes, QR redemptions, role grants, users, platform accounts, jobs and backup
-- runs keep the real now(), so application code can never backdate them.
-- tests/test_clock.py proves it and fails if a table is added without a decision.
-- =============================================================================
CREATE FUNCTION app_now() RETURNS timestamptz
  LANGUAGE sql STABLE PARALLEL SAFE
AS $$ SELECT coalesce(nullif(current_setting('app.now', true), '')::timestamptz, now()) $$;

REVOKE ALL ON FUNCTION app_now() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_now() TO hms_app, hms_worker, hms_platform, hms_backup;

-- Column defaults on business tables.
DO $$
DECLARE
  business text[] := ARRAY[
    'academic_sessions', 'academic_terms', 'announcements', 'asset_assignments', 'assets',
    'attendance_records', 'attendance_sessions', 'bed_allocations', 'beds', 'buildings',
    'complaint_attachments', 'complaint_categories', 'complaint_updates', 'complaints',
    'fee_structures', 'files', 'floors', 'gate_events', 'gate_passes', 'hostels',
    'inventory_items', 'leave_requests', 'ledger_allocations', 'ledger_entries', 'mess_menus',
    'notification_preferences', 'notifications', 'organization_profiles', 'organizations',
    'payment_reversals', 'payment_submissions', 'payments', 'refund_requests', 'room_types',
    'rooms', 'staff_members', 'stock_movements', 'student_documents', 'student_guardians',
    'students'];
  col record;
BEGIN
  FOR col IN
    SELECT table_name, column_name FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = ANY (business) AND column_default = 'now()'
  LOOP
    EXECUTE format('ALTER TABLE %I ALTER COLUMN %I SET DEFAULT app_now()', col.table_name, col.column_name);
  END LOOP;
END $$;

-- The bed-state view: "today" is the organization's business date.
CREATE OR REPLACE VIEW bed_states WITH (security_invoker = true) AS
SELECT
  b.id        AS bed_id,
  b.org_id,
  b.hostel_id,
  b.room_id,
  CASE
    WHEN b.status = 'inactive'    OR r.status = 'inactive'    THEN 'inactive'
    WHEN b.status = 'maintenance' OR r.status = 'maintenance' THEN 'maintenance'
    WHEN EXISTS (SELECT 1 FROM bed_allocations a
                  WHERE a.bed_id = b.id AND a.status = 'active' AND a.stay @> d.local_today)
      THEN 'occupied'
    WHEN EXISTS (SELECT 1 FROM bed_allocations a
                  WHERE a.bed_id = b.id AND a.status = 'reserved'
                    AND (a.end_date IS NULL OR a.end_date > d.local_today))
      THEN 'reserved'
    ELSE 'vacant'
  END AS state
FROM beds b
JOIN rooms r         ON r.id = b.room_id
JOIN organizations o ON o.id = b.org_id
CROSS JOIN LATERAL (SELECT (app_now() AT TIME ZONE o.timezone)::date AS local_today) d;

-- Inventory: a stock movement touches its item (a business row).
CREATE OR REPLACE FUNCTION apply_stock_movement() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  UPDATE inventory_items
     SET quantity_on_hand = quantity_on_hand + NEW.quantity, updated_at = app_now()
   WHERE id = NEW.item_id AND org_id = NEW.org_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'inventory item % not found', NEW.item_id USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NULL;
END $$;
