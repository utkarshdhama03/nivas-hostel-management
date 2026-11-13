-- =============================================================================
-- 50_grants.sql - what each role may do, table by table (docs/03 §2, §6).
--
-- RLS (40_rls.sql) decides WHICH ROWS a role sees. Grants decide WHICH TABLES
-- AND COLUMNS it may touch at all. Both must allow an action for it to happen.
--   * Nobody gets DELETE on business data. Records change status instead.
--   * Append-only tables: SELECT + INSERT only.
--   * Column grants limit a few UPDATEs to specific columns, and mask personal
--     data from the Platform Owner (Q15, Q55).
-- =============================================================================

-- --- hms_app and hms_worker: mutable tenant tables (SELECT, INSERT, UPDATE) -----------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'organization_profiles', 'academic_sessions', 'academic_terms', 'org_counters',
    'hostels', 'buildings', 'floors', 'room_types', 'rooms', 'beds',
    'users', 'user_recovery_codes', 'sessions', 'auth_tokens', 'email_otps',
    'staff_members', 'role_assignments', 'files', 'students', 'student_guardians', 'student_documents',
    'bed_allocations', 'fee_structures', 'payment_submissions', 'refund_requests',
    'attendance_sessions', 'attendance_records', 'leave_requests', 'gate_passes',
    'complaint_categories', 'complaints', 'mess_menus', 'assets', 'asset_assignments',
    'notifications', 'notification_preferences', 'announcements']
  LOOP
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I TO hms_app, hms_worker', t);
  END LOOP;

  -- --- Append-only tables (SELECT, INSERT) ---------------------------------------------
  FOREACH t IN ARRAY ARRAY[
    'ledger_entries', 'ledger_allocations', 'payments', 'payment_reversals', 'stock_movements',
    'complaint_updates', 'complaint_attachments', 'qr_redemptions', 'gate_events',
    'login_attempts', 'audit_logs', 'jobs']
  LOOP
    EXECUTE format('GRANT SELECT, INSERT ON %I TO hms_app, hms_worker', t);
  END LOOP;
END $$;

-- --- Limited UPDATEs (column grants) ---------------------------------------------------
-- organizations: the app may rename, change timezone, and activate after email
-- verification. Suspension columns belong to the Platform Owner only.
GRANT SELECT, INSERT ON organizations TO hms_app, hms_worker;
GRANT UPDATE (name, timezone, status, email_verified_at, updated_at, version)
  ON organizations TO hms_app, hms_worker;

-- inventory_items: quantity_on_hand changes ONLY through stock_movements (trigger).
GRANT SELECT, INSERT ON inventory_items TO hms_app, hms_worker;
GRANT UPDATE (name, unit, reorder_level, is_active, updated_at, version)
  ON inventory_items TO hms_app, hms_worker;

-- gate_events: only the warden's one-time acknowledgement (trigger enforces "once").
GRANT UPDATE (acknowledged_by, acknowledged_at) ON gate_events TO hms_app, hms_worker;

-- ledger_allocations: only the one-time release (trigger enforces "once").
GRANT UPDATE (released_at, released_by, release_reason) ON ledger_allocations TO hms_app, hms_worker;

-- --- hms_worker extras -----------------------------------------------------------------
-- Claims and finishes jobs; purges expiring security data. Nothing else is deletable.
GRANT UPDATE ON jobs TO hms_worker;
GRANT DELETE ON jobs, login_attempts, qr_redemptions, email_otps, auth_tokens TO hms_worker;

-- --- Derived view ------------------------------------------------------------------------
GRANT SELECT ON bed_states TO hms_app, hms_worker;

-- --- hms_platform: its own tables ----------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON platform_users, platform_recovery_codes, platform_sessions, platform_settings
  TO hms_platform;
GRANT SELECT ON backup_runs TO hms_platform;

-- --- hms_platform: the read window into tenants (column grants = masking) -------------
GRANT SELECT ON organizations TO hms_platform;
GRANT UPDATE (status, suspended_at, suspension_reason, updated_at, version) ON organizations TO hms_platform;
GRANT SELECT (org_id, legal_name, contact_email, contact_phone, city, state) ON organization_profiles TO hms_platform;
GRANT SELECT (id, org_id, is_active, created_at)                          ON hostels         TO hms_platform;
GRANT SELECT (id, org_id, hostel_id, status)                              ON beds            TO hms_platform;
GRANT SELECT (id, org_id, hostel_id, status, start_date, end_date)        ON bed_allocations TO hms_platform;
GRANT SELECT (id, org_id, hostel_id, status, created_at, exited_on)       ON students        TO hms_platform;
GRANT SELECT (id, org_id, kind, status, created_at, last_login_at)        ON users           TO hms_platform;
GRANT SELECT (org_id, succeeded, attempted_at)                            ON login_attempts  TO hms_platform;
GRANT SELECT (id, org_id, kind, priority, status, run_at, attempts, created_at, finished_at)
  ON jobs TO hms_platform;
-- Audit log: everything EXCEPT before_values, after_values, ip_address, user_agent (Q55).
GRANT SELECT (id, org_id, hostel_id, occurred_at, actor_type, actor_user_id, actor_platform_user_id,
              actor_roles, action, entity_type, entity_id, changed_fields, request_id)
  ON audit_logs TO hms_platform;
GRANT INSERT ON audit_logs TO hms_platform;

-- --- hms_backup ----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON backup_runs TO hms_backup;
