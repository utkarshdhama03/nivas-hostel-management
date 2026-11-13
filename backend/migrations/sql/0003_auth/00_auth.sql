-- =============================================================================
-- 0003_auth - small schema additions found while building authentication (Phase 1.4-1.5).
-- =============================================================================

-- 1. Two more reasons a session can end (04 §5.4, §5.8):
--    mfa_failed        5 wrong MFA codes cancelled a pending login (Q72)
--    password_changed  the password was changed or reset, so other sessions end
ALTER TABLE sessions DROP CONSTRAINT sessions_revoke_reason_check;
ALTER TABLE sessions ADD CONSTRAINT sessions_revoke_reason_check
  CHECK (revoke_reason IN ('logout','rotated','reuse_detected','admin','deactivated','expired',
                           'mfa_failed','password_changed'));
ALTER TABLE platform_sessions DROP CONSTRAINT platform_sessions_revoke_reason_check;
ALTER TABLE platform_sessions ADD CONSTRAINT platform_sessions_revoke_reason_check
  CHECK (revoke_reason IN ('logout','rotated','reuse_detected','admin','expired',
                           'mfa_failed','password_changed'));

-- 2. The Super Admin MFA reset (Q16, 05 AUTH-15) must also TELL the Super Admin.
--    hms_platform has no access to notifications or jobs, so the function (running as
--    the owner, under the organization's tenant context, RLS still forced) writes an
--    in-app security notification and queues its email in the same transaction.
--    The email job carries only the notification's ID, like every other job.
CREATE OR REPLACE FUNCTION platform_reset_super_admin_mfa(
    p_org_id           uuid,
    p_email            text,
    p_reason           text,
    p_platform_user_id uuid
) RETURNS void
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = public, pg_temp
AS $$
DECLARE
  v_user         uuid;
  v_notification uuid;
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

  INSERT INTO notifications (org_id, user_id, category, title, body)
  VALUES (p_org_id, v_user, 'security', 'Your two-step verification was reset',
          'The platform administrator reset your authenticator app at your request. '
          || 'Sign in with your password and set up a new authenticator. '
          || 'If you did not ask for this, contact the platform administrator immediately.')
  RETURNING id INTO v_notification;
  INSERT INTO jobs (org_id, kind, priority, payload)
  VALUES (p_org_id, 'email.notification', 0, jsonb_build_object('notification_id', v_notification::text));

  PERFORM set_config('app.org_id', '', true);   -- leave no tenant context behind
END $$;
-- CREATE OR REPLACE keeps the existing privileges (EXECUTE for hms_platform only).
