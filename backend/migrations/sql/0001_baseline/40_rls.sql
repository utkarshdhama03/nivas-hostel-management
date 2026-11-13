-- =============================================================================
-- 40_rls.sql - Row-Level Security (docs/03 §6, docs/02 §8).
--
-- ENABLE turns RLS on. FORCE makes it apply to the table OWNER too (owners and
-- superusers normally bypass RLS). A policy with no "TO" clause applies to every
-- role. With app.org_id unset, app_current_org() is NULL, "org_id = NULL" is
-- never true, so every query returns ZERO rows: the system fails closed.
-- =============================================================================

-- --- Standard tenant policy: the 48 tables with "org_id NOT NULL" -------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'organization_profiles', 'academic_sessions', 'academic_terms', 'org_counters',
    'hostels', 'buildings', 'floors', 'room_types', 'rooms', 'beds',
    'users', 'user_recovery_codes', 'sessions', 'auth_tokens', 'email_otps', 'login_attempts',
    'staff_members', 'role_assignments', 'files', 'students', 'student_guardians', 'student_documents',
    'bed_allocations', 'fee_structures', 'payment_submissions', 'payments', 'payment_reversals',
    'refund_requests', 'ledger_entries', 'ledger_allocations',
    'attendance_sessions', 'attendance_records', 'qr_redemptions',
    'leave_requests', 'gate_passes', 'gate_events',
    'complaint_categories', 'complaints', 'complaint_updates', 'complaint_attachments',
    'mess_menus', 'inventory_items', 'stock_movements', 'assets', 'asset_assignments',
    'notifications', 'notification_preferences', 'announcements']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I
                      USING (org_id = app_current_org())
                      WITH CHECK (org_id = app_current_org())', t);
  END LOOP;
END $$;

-- --- organizations: readable by all (slug lookup before login); writable for your own org
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY directory_read  ON organizations FOR SELECT USING (true);
CREATE POLICY register        ON organizations FOR INSERT TO hms_app
  WITH CHECK (status = 'pending_verification');
CREATE POLICY own_org_update  ON organizations FOR UPDATE TO hms_app
  USING (id = app_current_org()) WITH CHECK (id = app_current_org());
CREATE POLICY platform_update ON organizations FOR UPDATE TO hms_platform
  USING (true) WITH CHECK (true);

-- --- jobs: tenants see their own; the worker claims any organization's ---------------
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON jobs
  USING (org_id = app_current_org()) WITH CHECK (org_id = app_current_org());
CREATE POLICY worker_all       ON jobs TO hms_worker USING (true) WITH CHECK (true);
CREATE POLICY platform_read    ON jobs FOR SELECT TO hms_platform USING (true);

-- --- audit_logs: tenants read/insert their own; worker and platform insert their events
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_read     ON audit_logs FOR SELECT USING (org_id = app_current_org());
CREATE POLICY tenant_insert   ON audit_logs FOR INSERT WITH CHECK (org_id = app_current_org());
CREATE POLICY worker_insert   ON audit_logs FOR INSERT TO hms_worker   WITH CHECK (actor_type = 'system');
CREATE POLICY platform_read   ON audit_logs FOR SELECT TO hms_platform USING (true);
CREATE POLICY platform_insert ON audit_logs FOR INSERT TO hms_platform WITH CHECK (actor_type = 'platform_user');

-- --- The Platform Owner's read window (Q15, Q55) ---------------------------------------
-- Row access to every organization on these tables only. WHICH COLUMNS can be
-- read is limited separately by column grants in 50_grants.sql, which is what
-- masks personal data.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['organization_profiles', 'hostels', 'beds', 'bed_allocations',
                           'students', 'users', 'login_attempts']
  LOOP
    EXECUTE format('CREATE POLICY platform_read ON %I FOR SELECT TO hms_platform USING (true)', t);
  END LOOP;
END $$;

-- Platform tables and backup_runs have no org_id and no RLS: they are protected
-- by privileges instead (hms_app and hms_worker get none; see 50_grants.sql).
