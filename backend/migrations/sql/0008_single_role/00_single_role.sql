-- =============================================================================
-- 0008_single_role - one organization role: Administrator (D75).
--
-- The role stays stored as 'super_admin' (shown as "Administrator"). This migration:
--   1. revokes every other role grant (hostel_manager, warden, accountant, security_guard);
--   2. deactivates every staff account left with no active role, and ends its sessions;
--   3. audits both, as the system, in each organization's own audit log;
--   4. makes sure no other role can ever be granted again;
--   5. lets a SOLE Administrator approve their own refund request (decision 2), and
--      keeps "a different person decides" everywhere else (Q30).
--
-- Row-Level Security is FORCEd on these tables, even for the owner running this migration, so each
-- organization is handled with its own tenant context (app.org_id), exactly as the application does.
-- =============================================================================

-- 1-3 need a revocation without a revoking user: the system did it, for a recorded reason.
ALTER TABLE role_assignments
  ADD COLUMN revoke_reason text CHECK (revoke_reason IN ('role_removed_d75'));
ALTER TABLE role_assignments DROP CONSTRAINT role_assignments_check2;   -- (revoked_at IS NULL) = (revoked_by IS NULL)
ALTER TABLE role_assignments ADD CONSTRAINT role_assignments_revocation_check CHECK (
  (revoked_at IS NULL AND revoked_by IS NULL AND revoke_reason IS NULL)
  OR (revoked_at IS NOT NULL AND (revoked_by IS NOT NULL OR revoke_reason IS NOT NULL))
);

DO $$
DECLARE
  org record;
  r record;
BEGIN
  FOR org IN SELECT id FROM organizations ORDER BY id LOOP
    PERFORM set_config('app.org_id', org.id::text, true);

    FOR r IN
      UPDATE role_assignments
         SET revoked_at = now(), revoke_reason = 'role_removed_d75'
       WHERE org_id = org.id AND revoked_at IS NULL AND role <> 'super_admin'
      RETURNING id, user_id, role, hostel_id
    LOOP
      INSERT INTO audit_logs (org_id, hostel_id, actor_type, action, entity_type, entity_id,
                              changed_fields, before_values, after_values)
      VALUES (org.id, r.hostel_id, 'system', 'role.revoked', 'role_assignment', r.id,
              '{revoked_at}', jsonb_build_object('role', r.role, 'hostel_id', r.hostel_id),
              jsonb_build_object('reason', 'role_removed_d75'));
    END LOOP;

    FOR r IN
      UPDATE users u
         SET status = 'deactivated', updated_at = now()
       WHERE u.org_id = org.id AND u.kind = 'staff' AND u.status <> 'deactivated'
         AND NOT EXISTS (SELECT 1 FROM role_assignments ra
                          WHERE ra.user_id = u.id AND ra.revoked_at IS NULL)
      RETURNING u.id
    LOOP
      UPDATE sessions SET revoked_at = now(), revoke_reason = 'deactivated'
       WHERE user_id = r.id AND revoked_at IS NULL;
      INSERT INTO audit_logs (org_id, actor_type, action, entity_type, entity_id,
                              changed_fields, after_values)
      VALUES (org.id, 'system', 'account.deactivated', 'user', r.id,
              '{status}', jsonb_build_object('status', 'deactivated', 'reason', 'role_removed_d75'));
    END LOOP;
  END LOOP;
  PERFORM set_config('app.org_id', '', true);
END $$;

-- 4. Only the Administrator role can be active. The old values stay allowed for the revoked
--    history rows (the role CHECK is unchanged); un-revoking one of them is refused.
ALTER TABLE role_assignments ADD CONSTRAINT role_assignments_only_administrator
  CHECK (role = 'super_admin' OR revoked_at IS NOT NULL);

-- 5. Refunds: the requester may decide only as the organization's sole active Administrator.
ALTER TABLE refund_requests ADD COLUMN self_approved boolean NOT NULL DEFAULT false;
ALTER TABLE refund_requests DROP CONSTRAINT refund_requests_check;     -- decided_by <> requested_by
ALTER TABLE refund_requests ADD CONSTRAINT refund_requests_second_person CHECK (
  decided_by IS NULL OR decided_by <> requested_by
  OR (self_approved AND status IN ('approved', 'paid'))
);
ALTER TABLE refund_requests ADD CONSTRAINT refund_requests_self_approved_check
  CHECK (NOT self_approved OR decided_by = requested_by);

CREATE FUNCTION refund_self_approval_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
  admins integer;
BEGIN
  IF TG_OP = 'INSERT' OR NOT OLD.self_approved THEN
    -- The organization row is the lock that adding, deactivating or re-activating an Administrator
    -- also takes (app/modules/staff/service.py lock_organization), so "only one" can't change
    -- while this approval is being written.
    PERFORM 1 FROM organizations WHERE id = NEW.org_id FOR UPDATE;
    SELECT count(*) INTO admins
      FROM role_assignments ra JOIN users u ON u.id = ra.user_id
     WHERE ra.org_id = NEW.org_id AND ra.role = 'super_admin' AND ra.revoked_at IS NULL
       AND u.status = 'active';
    IF admins <> 1 OR TG_OP = 'INSERT' THEN
      RAISE EXCEPTION 'a refund needs another administrator''s approval'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'refund_requests_second_person';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER refund_self_approval_guard
  BEFORE INSERT OR UPDATE OF self_approved ON refund_requests
  FOR EACH ROW WHEN (NEW.self_approved) EXECUTE FUNCTION refund_self_approval_guard();
