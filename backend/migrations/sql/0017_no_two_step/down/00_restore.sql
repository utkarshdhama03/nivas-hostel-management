-- =============================================================================
-- Downgrade of 0017_no_two_step: the two-step structure as migrations 0001-0016 left it (taken from
-- pg_dump --schema-only of hms at 0016). Columns come back at the end of their tables; secrets,
-- recovery codes and session marks don't come back.
-- =============================================================================

ALTER TABLE email_otps DROP CONSTRAINT email_otps_cancelled_after_five;
ALTER TABLE email_otps DROP CONSTRAINT email_otps_purpose_check;
ALTER TABLE email_otps ALTER COLUMN purpose SET DEFAULT 'sign_in';
ALTER TABLE email_otps ADD CONSTRAINT email_otps_purpose_check CHECK (purpose IN ('sign_in', 'password_reset'));
ALTER TABLE email_otps ADD CONSTRAINT email_otps_cancelled_after_five
  CHECK (attempts < 5 OR consumed_at IS NOT NULL OR cancelled_at IS NOT NULL OR purpose = 'sign_in');

ALTER TABLE sessions
  ADD COLUMN mfa_verified_at timestamptz,
  ADD COLUMN mfa_skipped boolean NOT NULL DEFAULT false;
ALTER TABLE sessions ADD CONSTRAINT sessions_mfa_skipped_check CHECK (NOT mfa_skipped OR mfa_verified_at IS NOT NULL);
ALTER TABLE platform_sessions
  ADD COLUMN mfa_verified_at timestamptz,
  ADD COLUMN mfa_skipped boolean NOT NULL DEFAULT false;
ALTER TABLE platform_sessions ADD CONSTRAINT platform_sessions_mfa_skipped_check
  CHECK (NOT mfa_skipped OR mfa_verified_at IS NOT NULL);

ALTER TABLE platform_users
  ADD COLUMN totp_secret_encrypted bytea,
  ADD COLUMN totp_key_id text,
  ADD COLUMN totp_enabled_at timestamptz,
  ADD COLUMN totp_last_used_step bigint;
ALTER TABLE platform_users
  ADD CONSTRAINT platform_users_check CHECK (totp_enabled_at IS NULL OR totp_secret_encrypted IS NOT NULL),
  ADD CONSTRAINT platform_users_check1 CHECK ((totp_secret_encrypted IS NULL) = (totp_key_id IS NULL)),
  ADD CONSTRAINT platform_users_totp_key_id_check CHECK (totp_key_id ~ '^[a-z0-9-]{1,32}$'),
  ADD CONSTRAINT platform_users_totp_last_used_step_check CHECK (totp_last_used_step >= 0);

ALTER TABLE users
  ADD COLUMN totp_secret_encrypted bytea,
  ADD COLUMN totp_key_id text,
  ADD COLUMN totp_enabled_at timestamptz,
  ADD COLUMN totp_last_used_step bigint,
  ADD COLUMN email_otp_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE users
  ADD CONSTRAINT users_check2 CHECK (totp_enabled_at IS NULL OR totp_secret_encrypted IS NOT NULL),
  ADD CONSTRAINT users_check3 CHECK ((totp_secret_encrypted IS NULL) = (totp_key_id IS NULL)),
  ADD CONSTRAINT users_check4 CHECK (NOT email_otp_enabled OR email IS NOT NULL),
  ADD CONSTRAINT users_totp_key_id_check CHECK (totp_key_id ~ '^[a-z0-9-]{1,32}$'),
  ADD CONSTRAINT users_totp_last_used_step_check CHECK (totp_last_used_step >= 0);

CREATE TABLE public.platform_recovery_codes (
    id uuid DEFAULT uuidv7() NOT NULL,
    platform_user_id uuid NOT NULL,
    code_hash text NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.platform_recovery_codes OWNER TO hms_owner;

CREATE TABLE public.user_recovery_codes (
    id uuid DEFAULT uuidv7() NOT NULL,
    org_id uuid NOT NULL,
    user_id uuid NOT NULL,
    code_hash text NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.user_recovery_codes FORCE ROW LEVEL SECURITY;

ALTER TABLE public.user_recovery_codes OWNER TO hms_owner;

ALTER TABLE ONLY public.platform_recovery_codes
    ADD CONSTRAINT platform_recovery_codes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.platform_recovery_codes
    ADD CONSTRAINT platform_recovery_codes_platform_user_id_code_hash_key UNIQUE (platform_user_id, code_hash);

ALTER TABLE ONLY public.user_recovery_codes
    ADD CONSTRAINT user_recovery_codes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.user_recovery_codes
    ADD CONSTRAINT user_recovery_codes_user_id_code_hash_key UNIQUE (user_id, code_hash);

ALTER TABLE ONLY public.platform_recovery_codes
    ADD CONSTRAINT platform_recovery_codes_platform_user_id_fkey FOREIGN KEY (platform_user_id) REFERENCES public.platform_users(id);

ALTER TABLE ONLY public.user_recovery_codes
    ADD CONSTRAINT user_recovery_codes_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);

ALTER TABLE ONLY public.user_recovery_codes
    ADD CONSTRAINT user_recovery_codes_org_id_user_id_fkey FOREIGN KEY (org_id, user_id) REFERENCES public.users(org_id, id);

CREATE POLICY tenant_isolation ON public.user_recovery_codes USING ((org_id = public.app_current_org())) WITH CHECK ((org_id = public.app_current_org()));

ALTER TABLE public.user_recovery_codes ENABLE ROW LEVEL SECURITY;

GRANT SELECT,INSERT,UPDATE ON TABLE public.platform_recovery_codes TO hms_platform;

GRANT SELECT,INSERT,UPDATE ON TABLE public.user_recovery_codes TO hms_app;
GRANT SELECT,INSERT,UPDATE ON TABLE public.user_recovery_codes TO hms_worker;

CREATE OR REPLACE FUNCTION public.platform_reset_super_admin_mfa(p_org_id uuid, p_email text, p_reason text, p_platform_user_id uuid)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
END $function$;

ALTER FUNCTION platform_reset_super_admin_mfa(uuid, text, text, uuid) OWNER TO hms_owner;
REVOKE ALL ON FUNCTION platform_reset_super_admin_mfa(uuid, text, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_reset_super_admin_mfa(uuid, text, text, uuid) TO hms_platform;
