-- =============================================================================
-- 60_platform_functions.sql - narrow, audited powers for the Platform Owner.
-- =============================================================================

-- Q16: a Super Admin who lost every 2FA factor is reset by the Platform Owner
-- after verifying their identity out of band.
--
-- Why a function instead of a grant: giving hms_platform UPDATE on users would
-- let the platform module change anything about any user. This function does
-- exactly one job and nothing else:
--   * only for an ACTIVE SUPER ADMIN of that organization (found by email);
--   * clears TOTP and recovery codes, revokes sessions;
--   * sets no password and returns nothing secret;
--   * writes an audit row the organization's Super Admins can see.
--
-- SECURITY DEFINER: runs with the owner's privileges. FORCE RLS still applies,
-- so the function sets app.org_id for the target organization, and clears it
-- again as its last step, so the platform connection never keeps a tenant
-- context. (A "SET app.org_id = ''" clause on the function would be neater, but
-- PostgreSQL only lets a superuser attach a custom setting to a function, and
-- hms_owner deliberately isn't one. If the function fails midway, the caller's
-- transaction or savepoint rolls back, and the transaction-local setting with it.)
-- SET search_path: standard protection for SECURITY DEFINER functions against
-- objects planted in other schemas.
CREATE FUNCTION platform_reset_super_admin_mfa(
    p_org_id           uuid,
    p_email            text,
    p_reason           text,
    p_platform_user_id uuid
) RETURNS void
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = public, pg_temp
AS $$
DECLARE v_user uuid;
BEGIN
  IF length(coalesce(trim(p_reason), '')) < 10 THEN
    RAISE EXCEPTION 'a reason of at least 10 characters is required' USING ERRCODE = 'check_violation';
  END IF;

  PERFORM set_config('app.org_id', p_org_id::text, true);

  SELECT u.id INTO v_user
    FROM users u
    JOIN role_assignments r ON r.user_id = u.id AND r.role = 'super_admin' AND r.revoked_at IS NULL
   WHERE u.org_id = p_org_id AND lower(u.email) = lower(p_email) AND u.status = 'active';
  IF v_user IS NULL THEN
    RAISE EXCEPTION 'no active Super Admin with that email in this organization'
      USING ERRCODE = 'no_data_found';
  END IF;

  UPDATE users
     SET totp_secret_encrypted = NULL, totp_key_id = NULL, totp_enabled_at = NULL,
         totp_last_used_step = NULL, updated_at = now(), version = version + 1
   WHERE id = v_user;
  UPDATE user_recovery_codes SET used_at = now() WHERE user_id = v_user AND used_at IS NULL;
  UPDATE sessions SET revoked_at = now(), revoke_reason = 'admin' WHERE user_id = v_user AND revoked_at IS NULL;

  INSERT INTO audit_logs (org_id, actor_type, actor_platform_user_id, action, entity_type, entity_id,
                          changed_fields)
  VALUES (p_org_id, 'platform_user', p_platform_user_id, 'user.mfa_reset', 'user', v_user,
          ARRAY['totp_secret_encrypted', 'recovery_codes', 'sessions']);

  PERFORM set_config('app.org_id', '', true);   -- leave no tenant context behind
END $$;

REVOKE ALL ON FUNCTION platform_reset_super_admin_mfa(uuid, text, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_reset_super_admin_mfa(uuid, text, text, uuid) TO hms_platform;
