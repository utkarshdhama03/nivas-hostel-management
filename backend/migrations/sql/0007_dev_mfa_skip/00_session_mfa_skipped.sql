-- =============================================================================
-- 0007_dev_mfa_skip - development-only "skip two-step verification" (D72).
--
-- A session opened while HMS_DEV_SKIP_MFA was on carries mfa_skipped = true.
-- The application refuses such a session as soon as the setting is off
-- (app/core/context.py, app/modules/platform/router.py and both refresh paths),
-- so turning the setting off takes effect at once instead of after up to 7 days.
-- A rotated session keeps the mark. Existing rows are false: nothing changes for them.
--
-- The CHECK keeps the mark honest: a session can only be "skipped" once it counts
-- as having passed the second step.
-- =============================================================================
ALTER TABLE sessions
  ADD COLUMN mfa_skipped boolean NOT NULL DEFAULT false,
  ADD CONSTRAINT sessions_mfa_skipped_check CHECK (NOT mfa_skipped OR mfa_verified_at IS NOT NULL);

ALTER TABLE platform_sessions
  ADD COLUMN mfa_skipped boolean NOT NULL DEFAULT false,
  ADD CONSTRAINT platform_sessions_mfa_skipped_check CHECK (NOT mfa_skipped OR mfa_verified_at IS NOT NULL);
