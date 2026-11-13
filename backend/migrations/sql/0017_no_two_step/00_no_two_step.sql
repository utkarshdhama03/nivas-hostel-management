-- =============================================================================
-- 0017_no_two_step (12 November 2026, D87): no two-step verification for anyone.
--
-- Sign-in is the email (or ID) and the password, for Administrators, students and the Platform
-- Owner. Gone: authenticator (TOTP) secrets, recovery codes, sign-in codes by email, the marks
-- on sessions (second step done; skipped by the development-only HMS_DEV_SKIP_MFA, D72), and the
-- Platform Owner's reset of an Administrator's authenticator. email_otps stays, for the emailed
-- password-reset code (D77) only.
--
-- Dropping a column drops its CHECKs and column grants with it. The downgrade
-- (down/00_restore.sql) recreates the structure; secrets, codes and marks don't come back.
-- =============================================================================

DROP FUNCTION platform_reset_super_admin_mfa(uuid, text, text, uuid);
DROP TABLE user_recovery_codes;
DROP TABLE platform_recovery_codes;

ALTER TABLE users
  DROP COLUMN totp_secret_encrypted,
  DROP COLUMN totp_key_id,
  DROP COLUMN totp_enabled_at,
  DROP COLUMN totp_last_used_step,
  DROP COLUMN email_otp_enabled;
ALTER TABLE platform_users
  DROP COLUMN totp_secret_encrypted,
  DROP COLUMN totp_key_id,
  DROP COLUMN totp_enabled_at,
  DROP COLUMN totp_last_used_step;
ALTER TABLE sessions DROP COLUMN mfa_skipped, DROP COLUMN mfa_verified_at;
ALTER TABLE platform_sessions DROP COLUMN mfa_skipped, DROP COLUMN mfa_verified_at;

-- email_otps: password-reset codes only. Sign-in codes are deleted per organization (RLS is FORCEd,
-- even for the owner running this).
DO $$
DECLARE org record;
BEGIN
  FOR org IN SELECT id FROM organizations ORDER BY id LOOP
    PERFORM set_config('app.org_id', org.id::text, true);
    DELETE FROM email_otps WHERE purpose = 'sign_in';
  END LOOP;
  PERFORM set_config('app.org_id', '', true);
END $$;
ALTER TABLE email_otps DROP CONSTRAINT email_otps_cancelled_after_five;
ALTER TABLE email_otps DROP CONSTRAINT email_otps_purpose_check;
ALTER TABLE email_otps ALTER COLUMN purpose SET DEFAULT 'password_reset';
ALTER TABLE email_otps ADD CONSTRAINT email_otps_purpose_check CHECK (purpose = 'password_reset');
ALTER TABLE email_otps ADD CONSTRAINT email_otps_cancelled_after_five
  CHECK (attempts < 5 OR consumed_at IS NOT NULL OR cancelled_at IS NOT NULL);
