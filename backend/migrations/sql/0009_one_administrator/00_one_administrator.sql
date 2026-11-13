-- =============================================================================
-- 0009_one_administrator - exactly one Administrator per organization (D76).
--
-- The Administrator is the person who registered the organization. This migration:
--   1. keeps, in each organization, ONE active Administrator grant: the registration's own (the one
--      granted by nobody), else the oldest; it revokes any other, for a recorded reason;
--   2. deactivates every staff account left with no active role, and ends its sessions;
--   3. audits both, as the system, in each organization's own audit log;
--   4. makes a second active Administrator grant impossible (a unique index).
--
-- Row-Level Security is FORCEd on these tables, even for the owner running this migration, so each
-- organization is handled with its own tenant context (app.org_id), exactly as the application does.
-- =============================================================================

ALTER TABLE role_assignments DROP CONSTRAINT role_assignments_revoke_reason_check;
ALTER TABLE role_assignments ADD CONSTRAINT role_assignments_revoke_reason_check
  CHECK (revoke_reason IN ('role_removed_d75', 'extra_administrator_d76'));

DO $$
DECLARE
  org record;
  r record;
BEGIN
  FOR org IN SELECT id FROM organizations ORDER BY id LOOP
    PERFORM set_config('app.org_id', org.id::text, true);

    FOR r IN
      UPDATE role_assignments ra
         SET revoked_at = now(), revoke_reason = 'extra_administrator_d76'
       WHERE ra.org_id = org.id AND ra.revoked_at IS NULL AND ra.role = 'super_admin'
         AND ra.id <> (SELECT k.id FROM role_assignments k
                        WHERE k.org_id = org.id AND k.revoked_at IS NULL AND k.role = 'super_admin'
                        ORDER BY (k.granted_by IS NOT NULL), k.granted_at, k.id
                        LIMIT 1)
      RETURNING ra.id, ra.user_id
    LOOP
      INSERT INTO audit_logs (org_id, actor_type, action, entity_type, entity_id,
                              changed_fields, before_values, after_values)
      VALUES (org.id, 'system', 'role.revoked', 'role_assignment', r.id,
              '{revoked_at}', jsonb_build_object('role', 'super_admin'),
              jsonb_build_object('reason', 'extra_administrator_d76'));
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
              '{status}', jsonb_build_object('status', 'deactivated', 'reason', 'extra_administrator_d76'));
    END LOOP;
  END LOOP;
  PERFORM set_config('app.org_id', '', true);
END $$;

-- 4. At most one active Administrator grant per organization, enforced by the database.
CREATE UNIQUE INDEX role_assignments_one_administrator
  ON role_assignments (org_id) WHERE role = 'super_admin' AND revoked_at IS NULL;
