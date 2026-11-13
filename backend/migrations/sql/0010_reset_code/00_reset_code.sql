-- =============================================================================
-- 0010_reset_code (D77): forgot password by an emailed 6-digit code, not a link.
--
-- * email_otps gains `purpose` ('sign_in' = the optional email second step, as before;
--   'password_reset' = the new reset code) and `cancelled_at` (a code is cancelled after
--   5 wrong attempts, or when a newer reset code is sent).
-- * At most one live reset code per user (partial unique index).
-- * The reset LINK is gone: live and old password_reset links are deleted, and
--   auth_tokens no longer accepts that purpose.
-- =============================================================================

ALTER TABLE email_otps
  ADD COLUMN purpose text NOT NULL DEFAULT 'sign_in',
  ADD COLUMN cancelled_at timestamptz;
ALTER TABLE email_otps
  ADD CONSTRAINT email_otps_purpose_check CHECK (purpose IN ('sign_in','password_reset')),
  ADD CONSTRAINT email_otps_used_or_cancelled CHECK (consumed_at IS NULL OR cancelled_at IS NULL),
  ADD CONSTRAINT email_otps_cancelled_after_five CHECK (attempts < 5 OR consumed_at IS NOT NULL OR cancelled_at IS NOT NULL OR purpose = 'sign_in');
CREATE UNIQUE INDEX email_otps_one_live_reset ON email_otps (user_id)
  WHERE purpose = 'password_reset' AND consumed_at IS NULL AND cancelled_at IS NULL;

-- auth_tokens is under FORCE RLS: delete the old reset links one organization at a time.
DO $$
DECLARE o uuid;
BEGIN
  FOR o IN SELECT id FROM organizations ORDER BY id LOOP
    PERFORM set_config('app.org_id', o::text, true);
    DELETE FROM auth_tokens WHERE purpose = 'password_reset';
  END LOOP;
  PERFORM set_config('app.org_id', '', true);
END $$;

ALTER TABLE auth_tokens DROP CONSTRAINT auth_tokens_purpose_check;
ALTER TABLE auth_tokens ADD CONSTRAINT auth_tokens_purpose_check CHECK (purpose IN ('activation','org_registration'));
