# 05 — Security Analysis

| | |
|---|---|
| Status | Describes the current product (12 November 2026). Based on decisions Q1–Q74 and D74–D88, [03-database-design.md](03-database-design.md) and [04-api-design.md](04-api-design.md). |
| Scope | Risks and protections for multi-tenancy, authentication, the financial ledger, leave, document uploads, role permissions and student registration, plus short sections on general web and operational risks. **Every risk names the test that proves its protection works.** |
| Date | 2026-11-07 |

**How to read the risk tables.** Each row is one way things could go wrong:

- **Rating:** **H** (high), **M** (medium) or **L** (low), combining how likely it is and how much damage it would do.
- **Protection:** what stops it, and which layer enforces it (DB = the database guarantees it even if application code is wrong).
- **Test:** the name of the automated test that must pass. The names were planned before the code; where one differs from the real test, [Appendix A](#appendix-a-test-traceability) maps it.

---

## 1. What we're protecting, and from whom

**Assets**

| ID | Asset | Why it matters |
|---|---|---|
| A1 | Student personal data (names, contacts, DOB, guardians, addresses) | Privacy; possibly minors' data (Q44) |
| A2 | Identity documents (masked Aadhaar PDFs, other IDs) | The most sensitive files in the system (Q62) |
| A3 | Financial records (ledger, payments, receipts, refunds, deposits) | Money and disputes; fraud target |
| A4 | Leave records | Student safety; reveals when someone is away |
| A5 | Credentials and sessions (passwords, tokens, emailed reset codes) | Keys to everything else |
| A6 | The audit trail | Evidence; must survive mistakes and misconduct |
| A7 | Availability | Fees, leave and complaints are handled through it every day |

**Attackers**

| ID | Who | Example |
|---|---|---|
| T1 | Anonymous internet user | Credential stuffing, registration spam (mostly relevant once hosted, Q1) |
| T2 | User of **another organization** | Changing an ID in a request to read someone else's students |
| T3 | Student of the same organization | Reading another student's fees, leave or complaints |
| T4 | The Administrator misusing their access (insider) | Fake waivers or reversals; reading documents without reason |
| T5 | The Administrator colluding with a student | Waiving fees without a record (every waiver needs a reason and is audited) |
| T6 | Platform Owner account (overreach or compromise) | Browsing students' personal data |
| T7 | Someone with the machine, disk or a backup | Reading files or the database directly |
| T8 | Compromised dependency | Malicious package version |

**Trust boundaries:** browser ↔ API (all input is untrusted) · API ↔ PostgreSQL (the database is the last line of defence: RLS, constraints, privileges) · API/worker ↔ file storage · worker ↔ email · database ↔ backup repository.

---

## 2. How the security tests are organized

- **Location:** `backend/tests/security/`, one module per area below. Tests that protect one feature live next to it (for example `test_finance.py`, `dbproof/`). There is no `security` marker: every test runs in the local quality gate on every check, and none is skipped. Where this document names a test that exists under another name, [Appendix A](#appendix-a-test-traceability) gives the real one.
- **Real PostgreSQL:** every test runs against PostgreSQL 18 in Docker, connected as the same roles the app uses (`hms_app`, `hms_worker`, `hms_platform`). RLS, constraints and privileges only behave correctly on the real thing.
- **The "two-organization world" fixture** (`two_org_world`) builds two organizations A and B. Each has two hostels; its Administrator; students in both hostels; and at least one record of **every resource type**: allocation, reservation, fee structure, charge, payment, allocation row, refund request, payment submission, leave request, complaint, mess menu, announcement, notification, audit row, and a file of every category.
- **Route classification meta-test:** `test_every_route_is_classified` walks FastAPI's route table. Every route must appear in a `SECURITY_MATRIX` (required permission, resource type, scope) or in an explicit `PUBLIC_ROUTES` list. **A new endpoint that nobody classified fails the build**, and once it's classified it automatically joins the cross-tenant and permission tests below.
- **Concurrency tests** open two real database connections and release them together with a `threading.Barrier`, so races actually happen instead of being simulated.
- **"Nothing changed" checks** snapshot the victim organization's rows through a separate owner-level test connection before and after an attack attempt, and compare them.

---

## 3. Multi-tenancy

| ID | Risk | Rating | Protection | Test(s) |
|---|---|---|---|---|
| MT-1 | A query forgets `org_id` and returns another organization's rows | **H** | ORM auto-filter (02 §8 layer 2) + **RLS with `FORCE`** on a non-owner role (DB) | `test_cross_tenant_read_returns_404[route]`, `test_cross_tenant_list_excludes_other_org[route]`, `test_rls_returns_zero_rows_without_context[table]` |
| MT-2 | Client sends another organization's IDs in a URL, body or filter (IDOR) | **H** | Tenant from the token only; IDs resolved under RLS; `org_id` never accepted in bodies (`extra="forbid"`); 404 instead of 403 | `test_cross_tenant_write_rejected[route]` (+ victim snapshot unchanged), `test_org_id_in_body_rejected`, `test_cross_tenant_filter_param_returns_404` |
| MT-3 | A row references another organization's row, e.g. an allocation on B's bed. Foreign-key checks **ignore RLS**, so a plain FK would allow it | **H** | Composite FKs `(org_id, …)` everywhere (DB) | `test_composite_fk_rejects_cross_org_reference[fk]` (SQL level, as `hms_app` in org A, inserting a reference to B's row) |
| MT-4 | Tenant setting leaks to the next request through a pooled connection | **H** | `set_config(..., true)` is transaction-local; pool resets on return | `test_tenant_setting_cleared_after_commit`, `test_alternating_orgs_on_one_pooled_connection` (pool size 1, requests from A and B interleaved; each sees only its own data) |
| MT-5 | Some code opens its own database session and skips the tenant dependency | M | Single session factory; fail-closed RLS | `test_only_core_db_creates_sessions` (static import check), plus the MT-1 zero-rows test |
| MT-6 | The app connects as the table owner or a superuser, which bypasses RLS | **H** | Startup check refuses to run if the role owns tables, is superuser or has `BYPASSRLS` | `test_app_roles_are_not_owner_superuser_or_bypassrls` |
| MT-7 | A new table is added without RLS | **H** | Schema lint | `test_every_org_table_has_forced_rls_and_policy`, `test_platform_tables_have_no_app_grants` |
| MT-8 | Unique constraints or error messages reveal another organization's data | M | Uniqueness scoped by `org_id`; generic errors (04 §7) | `test_same_email_allowed_in_two_orgs`, `test_same_student_code_allowed_in_two_orgs`, `test_error_bodies_never_mention_other_org` (scans every response from the cross-tenant suite for B's names, codes and IDs) |
| MT-9 | A background job runs under the wrong tenant, or none | **H** | Worker sets context from the job row; payloads carry IDs only | `test_worker_sets_job_org_context`, `test_job_for_org_a_cannot_touch_org_b` (A's job whose payload names B's student does nothing to B) |
| MT-10 | The Platform Owner reads personal data | **H** | `hms_platform` has **no BYPASSRLS**; explicit policies + column grants (03 §6) | `test_platform_role_has_no_bypassrls`, `test_platform_cannot_select_masked_columns[table.column]`, `test_platform_audit_view_masks_values`, `test_platform_cannot_read_files_or_documents` |
| MT-11 | Logging into the wrong organization; probing users across organizations | M | Identifiers looked up only inside the slug's organization (04 §4.2) | `test_login_with_other_org_slug_fails`, `test_login_unknown_slug_generic_error` |
| MT-12 | A refresh cookie with a swapped org ID | M | Secret hash must match the session in that org | `test_refresh_cookie_org_swap_rejected` |
| MT-13 | A file from another organization via a guessed or reused link | **H** | Downloads go through the owning record under RLS; signed links bound to user + session | `test_file_link_cross_tenant_404`, `test_signed_link_rejected_for_other_user` |
| MT-15 | Exports, the home overview or search include other organizations | M | Same RLS path; no raw cross-tenant queries outside `platform` | `test_overview_is_for_the_organization_only`, `test_search_never_returns_other_org` |
| MT-16 | A suspended organization keeps working | M | Status checked on every request; worker skips suspended organizations | `test_suspended_org_requests_rejected`, `test_suspended_org_jobs_not_run` |

### 3.1 The cross-tenant isolation test plan in detail

These tests are the heart of the security suite. They are **generated from the route table**, so coverage grows automatically as endpoints are added.

**Setup:** `two_org_world` (§2). The attacker is Org A's Administrator, then one of Org A's students; the victim is Org B.

**For every classified route that takes a resource ID**, the suite substitutes Org B's ID of the right resource type and checks:

| Action | Attacker | Expected | Also asserted |
|---|---|---|---|
| Read one (`GET /…/{id}`) | A's Administrator | `404 not_found` | Body has no B data (MT-8) |
| List / search / filter (`GET /…?hostel_id=B's`) | each A role | `404` for a foreign filter ID; otherwise results contain **only A rows** | Every returned `id` belongs to A (checked through the owner connection) |
| Create referencing B (`POST` with B's `student_id`, `bed_id`, `hostel_id`, `file_id`, …) | A's Administrator | `404` or `422` | **B snapshot unchanged**; no row created in A pointing at B |
| Update / action (`PATCH`, `POST …/approve`, `…/reverse`, …) | A's Administrator | `404` | B snapshot unchanged |
| File link / content | each A role | `404` | No signed link issued |
| Export | each A role | CSV contains only A rows | Row count matches A's own |

**Below the API**, SQL-level tests prove the database holds the line on its own, so even a future code bug can't leak:

| Test | What it does |
|---|---|
| `test_rls_returns_zero_rows_without_context[table]` | As `hms_app` with no `app.org_id`: every tenant table returns 0 rows. |
| `test_rls_hides_other_org_rows[table]` | As `hms_app` with context A: no row with B's `org_id` is visible. |
| `test_rls_rejects_insert_for_other_org[table]` | As `hms_app` with context A: inserting a row with `org_id = B` fails (`WITH CHECK`). |
| `test_rls_rejects_update_into_other_org[table]` | Changing a row's `org_id` from A to B fails. |
| `test_composite_fk_rejects_cross_org_reference[fk]` | Every composite FK rejects a reference to B's row. |
| `test_every_org_table_has_forced_rls_and_policy` | Reads `pg_class` / `pg_policies`: RLS enabled **and** forced, with a policy, on every table with `org_id`. |
| `test_platform_cannot_select_masked_columns[table.column]` | As `hms_platform`: every column outside the read window (03 §6) raises a permission error. |
| `test_worker_claim_policy_only_on_jobs` | `hms_worker`'s cross-organization policy exists on `jobs` and nowhere else. |

**Coverage rule:** a route can only skip the cross-tenant suite by being listed in `PUBLIC_ROUTES` with a written reason. That list is reviewed like code.

---

## 4. Authentication

| ID | Risk | Rating | Protection | Test(s) |
|---|---|---|---|---|
| AUTH-1 | Password guessing / credential stuffing | **H** | Argon2id; per-account lock and per-IP throttle (Q72); password rules (Q71, D87): at least 8 characters for everyone, blocklist + context words, no composition rules, no expiry | `test_login_lockout_after_5_failures`, `test_ip_throttle_after_20_failures`, `test_eight_characters_for_everyone`, `test_password_blocklist_and_context_words`, `test_no_composition_rules_imposed`, `test_long_unicode_passphrase_accepted` |
| AUTH-2 | Finding out which accounts exist (login, reset, registration) | M | Identical responses; dummy Argon2 run for unknown users; reset always 202; every wrong reset code, unknown account or organization gets the same `invalid_reset_code` (D77) | `test_login_unknown_user_same_response_as_wrong_password`, `test_unknown_user_still_runs_password_hash`, `test_password_reset_always_202`, `test_password_reset_unknown_account_gets_the_same_answer` |
| AUTH-3 | Stolen access token | M | 15-minute lifetime; memory-only; session re-checked every request | `test_access_token_rejected_after_session_revoked`, `test_expired_access_token_rejected` |
| AUTH-4 | Stolen refresh token reused | **H** | Rotation + reuse detection (revokes the family); 10-second grace for tab races only | `test_refresh_rotates_token`, `test_refresh_reuse_revokes_family`, `test_refresh_reuse_within_grace_does_not_revoke` |
| AUTH-5 | Forged JWT / algorithm confusion / token used in the wrong place | **H** | Algorithm pinned; keys from env with `kid`; `iss`, `aud`, `typ` checked; separate platform key | `test_jwt_alg_none_rejected`, `test_jwt_wrong_key_rejected`, `test_org_token_rejected_on_platform_routes`, `test_platform_token_rejected_on_org_routes` |
| AUTH-9 | Activation link guessed, intercepted or reused (the reset link is gone since D77, see AUTH-17) | M | 256-bit random tokens, stored hashed; single-use; expiry; one live link per user (DB); token in the URL fragment | `test_emailed_activation_then_login` (single use), `test_the_activation_email_can_be_sent_again` (a new link revokes the old), `test_password_reset_revokes_all_sessions` |
| AUTH-10 | CSRF against cookie endpoints; session fixation | M | Cookie only on `/auth/refresh` and `/auth/logout`, `SameSite=Strict` + required header + `Origin` check; new session on every login | `test_refresh_requires_custom_header`, `test_refresh_rejects_foreign_origin`, `test_login_always_creates_new_session` |
| AUTH-11 | Registration spam / fake organizations | M | Rate limits; email verification; Platform Owner suspension | `test_registration_rate_limited`, `test_an_unverified_organization_can_not_sign_in` |
| AUTH-12 | Secrets end up in logs or audit rows | **H** | Log redaction filter; audit field allowlists; no request bodies logged on auth routes | `test_no_secrets_in_logs_or_audit` (runs every auth flow with sentinel passwords, codes and tokens, then searches all captured logs and audit rows for them) |
| AUTH-14 | Platform Owner account compromise | **H** | Email and password only since D87, so the password matters most: 8+ characters, lock after 5 wrong tries, 8-hour sessions, separate keys; `hms_platform`'s narrow database window limits the damage (MT-10); a forgotten password is reset only on the server | `test_platform_sign_in_is_email_and_password`, `test_wrong_passwords_lock_the_account`, `test_console_password_reset_ends_every_session` + the MT-10 tests |
| AUTH-18 | Organization sign-in by email only (D78): finding which emails are Administrators, or reading another organization's data through the lookup | M | One `401 invalid_credentials` for unknown email, wrong password, student email and unverified organization, after the same Argon2 work; account lock and IP throttle unchanged; the lookup is a SECURITY DEFINER function that returns one organization ID for one exact email; the table behind it has FORCEd RLS with an owner-only policy and no privilege for the application roles; no other RLS changed. Registration does say an email is taken (accepted, D78; rate-limited) | `test_unknown_email_and_wrong_password_answer_the_same`, `test_limits_stay`, `test_the_lookup_is_narrow_and_rls_is_unchanged`, `test_administrator_emails_are_unique_across_the_platform`, `test_an_unverified_organization_can_not_sign_in`, `test_forgot_password_with_only_the_email`, `test_old_links_and_student_sign_in_are_unchanged` |
| AUTH-19 | Student sign-in by email only (D80): finding which emails are students, or reaching another organization through the lookup | M | As AUTH-18: one `401 invalid_credentials`, the same limits; `student_org()` returns one organization ID for one exact email; its tables have FORCEd RLS with an owner-only policy; no other RLS changed. Admission and registration do say a student email is taken (accepted, D80) | `test_unknown_email_and_wrong_password_answer_the_same`, `test_limits_stay`, `test_the_lookup_is_narrow_and_rls_is_unchanged`, `test_student_emails_are_unique_across_the_platform`, `test_a_registered_student_is_told_how_the_request_stands`, `test_the_old_link_with_the_student_id_still_works` |
| AUTH-17 | Password-reset code (D77) guessed, reused, or used to sign in | M | 6 digits from a CSPRNG, Argon2-hashed; 10 minutes; single use; 5 wrong attempts cancel it (database CHECK too); a new code cancels the old one (one live code, DB index); 20 wrong codes per IP per 15 min; 1 send a minute and 3 an hour per account; not in the email subject | `test_password_reset_flow`, `test_password_reset_code_expires_after_ten_minutes`, `test_password_reset_code_cancelled_after_five_wrong_attempts`, `test_password_reset_new_code_cancels_the_old_one`, `test_password_reset_requests_rate_limited`, `test_password_reset_wrong_codes_rate_limited_per_ip`, `test_reset_links_are_gone_from_the_database` |

---

## 5. Financial ledger

| ID | Risk | Rating | Protection | Test(s) |
|---|---|---|---|---|
| FIN-1 | History edited or deleted (bug or insider) | **H** | No UPDATE/DELETE privilege + append-only triggers on ledger, payments, reversals (DB) | `test_financial_tables_reject_update_and_delete[table]` (as `hms_app` *and* as the owner, which hits the trigger) |
| FIN-2 | Payment recorded twice (double-click, retry, two tabs) | **H** | `Idempotency-Key`; unique reference; unique pending UTR (DB) | `test_payment_retry_with_same_key_returns_original`, `test_same_reference_twice_rejected`, `test_concurrent_identical_payments_only_one_succeeds` |
| FIN-3 | A payment's ledger entries don't add up to the payment | **H** | Deferred constraint trigger (DB) | `test_payment_entries_must_sum_to_amount`, `test_payment_without_entries_rejected` |
| FIN-4 | Wrong sign or account for an entry type | M | `ledger_sign_rules` CHECK (DB) | `test_ledger_sign_rules[entry_type]` |
| FIN-5 | Bad reversals: partial, twice, reversing a reversal, half-reversed payment | M | `reverses_entry_id UNIQUE` + `ledger_guard` + deferred check (DB) | `test_reversal_must_be_exact_opposite`, `test_entry_reversed_only_once`, `test_cannot_reverse_a_reversal`, `test_reversed_payment_has_all_entries_reversed` |
| FIN-6 | Allocation errors: more than the payment or charge, across students, on reversed entries (Q60) | **H** | Composite FKs + `ledger_allocations_guard` under the per-student lock + deferred release check (DB) | `test_allocation_cannot_exceed_credit`, `test_allocation_cannot_exceed_charge`, `test_allocation_same_student_only`, `test_concurrent_allocations_respect_limits`, `test_reversal_releases_allocations`, `test_released_allocation_cannot_be_reactivated`, `test_no_open_charge_and_unallocated_credit_together` (reconciliation) |
| FIN-7 | Deposit refunded twice or beyond what's held | **H** | Balance floor under the per-student lock; one ledger entry per refund request (DB) | `test_concurrent_deposit_refunds_only_one_succeeds`, `test_refund_requires_approved_request`, `test_refund_paid_once` |
| FIN-8 | One person approves their own refund (Q30) | **H** | `refund_requests_second_person` (DB): the approver differs from the requester, **except** the organization's sole active Administrator (D76), whose approval is marked `self_approved` and audited; the trigger `refund_self_approval_guard` checks it under the organization-row lock | `test_refund_self_approval_rejected_even_for_super_admin` |
| FIN-9 | Receipt gaps or duplicates, e.g. to hide a cash payment | **H** | Counter row updated in the same transaction; generated receipt number; append-only payments (DB) | `test_receipt_numbers_gap_free_when_payment_fails` (forces a failure after the counter increment; the next receipt reuses that number), `test_receipt_numbers_unique_under_concurrency`, `test_receipt_number_matches_sequence` |
| FIN-10 | Rounding errors | M | `numeric(12,2)`, `Decimal`, money as strings in JSON | `test_money_serialized_as_string`, `test_money_as_json_number_rejected` |
| FIN-11 | Late-fee job double-charges or exceeds the cap on rerun | M | Partial unique keys (DB); cap applied by the job | `test_late_fee_job_idempotent`, `test_late_fee_respects_cap`, `test_charge_run_idempotent` |
| FIN-12 | Fake UPI submissions by students | M | The Administrator's verification (process: check against the bank statement); unique UTR; reviewer ≠ submitter (DB) | `test_duplicate_utr_rejected`, `test_submission_reviewed_by_other_user`, `test_rejected_submission_requires_note` |
| FIN-13 | Administrator insider: fake waivers or reversals | M | Permission checks; reason required (DB); everything audited; waiver/reversal report by user | `test_waiver_requires_reason`, `test_waiver_requires_permission[role]`, `test_reversal_creates_audit_row` |
| FIN-14 | A fee structure edit silently changes past charges | L | Charges copy the amount; amount edits blocked once used | `test_fee_structure_amount_locked_after_use` |
| FIN-15 | Another organization's finances | **H** | Covered by §3.1 (ledger, payments, refunds and receipts are all classified routes) | Cross-tenant suite |
| FIN-16 | A waiver lands on the wrong charge or exceeds it (Q64) | M | Waiver requires a target charge; `manual` allocation in the same transaction; guard + deferred trigger (DB) | `test_waiver_requires_target_charge`, `test_waiver_cannot_exceed_target_outstanding`, `test_waiver_left_unallocated_rejected_at_commit` |
| FIN-17 | Two tabs edit the same fee structure; one silently overwrites the other (Q70) | M | `version` compare-and-increment → `409 edit_conflict` | `test_stale_version_update_returns_409[editable_table]` |

---

## 7. Leave

Approved leave is a record the Administrator and the student can both see. (The IDs keep their original GATE- prefix so references stay stable.)

| ID | Risk | Rating | Protection | Test(s) |
|---|---|---|---|---|
| GATE-6 | Leave approved by the wrong person | M | Permission `leave.manage` (the Administrator); students can't approve | `test_student_cannot_approve_leave` |
| GATE-8 | Overlapping leaves for one student | L | Exclusion constraint `leave_no_overlap` (DB) | `test_overlapping_leave_rejected` |

---

## 8. Document uploads (Q44, Q45, Q62)

| ID | Risk | Rating | Protection | Test(s) |
|---|---|---|---|---|
| DOC-1 | Malicious file disguised as an allowed type (HTML/SVG/script, polyglots) | **H** | Type detected from content; allowlist (no SVG/HTML); images re-encoded; served with `nosniff`, `sandbox` CSP and `attachment` | `test_upload_rejects_disguised_types[html_as_jpg, svg, exe_as_pdf]`, `test_fake_pdf_without_header_or_trailer_rejected` (Q66), `test_images_reencoded_and_exif_stripped`, `test_file_content_security_headers` |
| DOC-2 | Oversized uploads / filling the disk | M | Streaming size limit; type/size CHECKs (DB) | `test_upload_over_limit_returns_413`, `test_db_rejects_oversized_file_row` |
| DOC-3 | Path traversal or header injection via file names | **H** | Server-generated storage keys + format CHECK (DB); original name only in `filename*` | `test_storage_key_format_enforced_by_db`, `test_malicious_original_name_sanitized` |
| DOC-4 | Downloading a file you shouldn't (another student's, or a staff-only file as a student) | **H** | Access through the owning record; per-category access table (03 §5.5) | `test_file_access_matrix[category × user]` |
| DOC-5 | Signed links leaked (history, referrer, shared) | M | 60-second expiry; bound to user + session; `no-referrer`; **never** for restricted files | `test_signed_link_expires`, `test_signed_link_bound_to_session`, `test_restricted_files_have_no_signed_links` |
| DOC-6 | **Full Aadhaar number exposed** (scanned card uploaded) | **H** | Aadhaar must be the masked UIDAI PDF: PDF only, masked number, uploader attestation (API 422 + DB CHECK); identity files restricted to the Administrator; every download audited; never exported (Q62) | `test_aadhaar_upload_requires_pdf_masked_number_and_attestation`, `test_db_rejects_aadhaar_image_or_unattested`, `test_identity_document_access_only_administrators`, `test_identity_document_download_audited`, `test_identity_documents_never_exported` |
| DOC-7 | Identity documents read from a copied disk or backup | M | AES-256-GCM at rest; key outside the database, project and backups; key ID per file; DB requires it for the category (Q65) | `test_identity_document_encrypted_at_rest` (bytes on disk ≠ plaintext and don't start with `%PDF`), `test_identity_document_records_key_id`, `test_db_rejects_unencrypted_identity_document`, `test_ciphertext_bound_to_file_record`, `test_decrypt_with_old_key_id_after_new_active_key` |
| DOC-8 | Unattached uploads used to stash files | L | Visible only to the uploader; purged after 24 h | `test_unattached_file_visible_only_to_uploader`, `test_unattached_files_purged` |
| DOC-9 | Malware reaching staff devices inside PDFs | M | No antivirus in v1 (Q45); PDFs always downloaded as attachments | **Residual** (§11) |
| DOC-10 | Wrong category: an Aadhaar card uploaded as an "admission document" (standard access) | M | Identity documents are forced into the restricted category (DB), but a mislabelled upload can't be detected; UI warns on the admission-document form | `test_identity_document_type_forces_restricted_category`; **residual** (§11) |

---

## 9. Role permissions

> An organization has one role, Administrator, held by exactly one person: the one who registered it (D75/D76; unique index `role_assignments_one_administrator`). The second-person rule for refunds (RBAC-6, FIN-8) becomes a marked, audited self-approval.

| ID | Risk | Rating | Protection | Test(s) |
|---|---|---|---|---|
| RBAC-1 | Vertical escalation through role grants (anyone grants themselves a role, or adds a second Administrator) | **H** | No API route grants a role (D76); no self-grant, no old role, at most one Administrator (DB) | `test_the_registering_administrator_is_the_only_one`, `test_database_refuses_old_roles_and_self_grants`, `test_database_refuses_a_second_administrator` |
| RBAC-2 | Mass assignment (sending `role`, `kind`, `org_id`, `status` in a PATCH) | **H** | `extra="forbid"`; separate request schemas per role | `test_patch_me_rejects_privileged_fields`, `test_unknown_fields_rejected[route]` |
| RBAC-3 | A student reads another student's data | **H** | Portal routes are "me" only; no student IDs in portal URLs | `test_portal_routes_have_no_id_params` (route lint), `test_student_cannot_call_admin_routes` |
| RBAC-5 | Permissions still work after a role is revoked or a user deactivated | M | Roles loaded per request; a deactivated account's sessions stop (migrations 0008/0009 revoke them too) | `test_deactivated_user`, `test_revoked_session_stops_working_immediately` |
| RBAC-6 | The Administrator bypasses second-person rules | **H** | With one Administrator (D76) a refund is self-approved: marked `self_approved` and audited; the DB refuses an unmarked self-decision | FIN-8 tests, `test_the_administrators_own_refund_is_self_approved_and_audited` |
| RBAC-7 | The Administrator removed (organization locked out) | M | No route deactivates a staff account (D76), and nobody acts on their own account; a forgotten password is reset with the emailed code (AUTH-17) | `test_the_registering_administrator_is_the_only_one` |
| RBAC-8 | Field-level overexposure | L | The Administrator sees the full record; students reach only their own data (RBAC-3) | `test_student_response_fields_by_role` |
| RBAC-9 | Exited (read-only) student writes data | L | Dependency allows GET only | `test_read_only_user_cannot_write` |
| RBAC-10 | Platform Owner acts inside an organization | **H** | No impersonation endpoints; only suspension writes tenant data | `test_platform_routes_write_only_suspension` + MT-10 tests |
| RBAC-11 | Security that only exists in the UI | **H** | Every route classified and tested at the API (§2) | `test_every_route_is_classified` + the permission matrix |

`test_permission_matrix` is generated from `SECURITY_MATRIX`. For every route it calls the endpoint as each role, with random IDs and empty bodies so that nothing is created. It checks that a role without the permission gets exactly 403, and that a role with it is **not** refused with 403 (the Administrator's blanket access, Q59, included). That proves the permission check, not that the action succeeds: the success paths are proven by each feature's own tests (`test_finance.py`, `test_allocations.py`...).

### 9a. Student registration requests (D74)

| ID | Threat | Severity | Mitigation | Tests |
|---|---|---|---|---|
| REG-1 | A request reaches another organization, or another organization's Administrator decides it | **H** | Organization from the address; RLS on the table; admin routes by session; cross-tenant 404 | `test_other_organizations_and_students_can_not_reach_requests` |
| REG-2 | Checking someone else's request or finding out which emails registered | M | No status page; sign-in reveals a request's state only with its correct password; a known email gets the same 202, the email tells its owner; an unknown organization looks like a closed one | `test_register_verify_wait_approve_then_sign_in`, `test_rejection_shows_the_reason_on_sign_in`, `test_a_known_email_gets_the_same_answer_and_an_email_instead`, `test_switch_is_off_by_default_and_an_unknown_organization_looks_the_same` |
| REG-3 | Spam requests or a flood of emails | M | Off by default; 5/hour per IP; 50/day per organization; one open request per email (DB); honeypot; email verification; unverified deleted after 24 hours | `test_rate_limits_per_ip_and_per_organization`, `test_the_honeypot_stores_nothing`, `test_the_database_keeps_one_open_request_per_email`, `test_purge_deletes_unverified_after_a_day_and_rejected_personal_data_after_30_days` |
| REG-4 | A malicious photo | M | The upload pipeline's checks (size, real type, JPEG/PNG only, re-encode drops metadata), resized to 800 px, served with `nosniff` to the Administrator only | `test_the_form_is_checked`, `test_register_verify_wait_approve_then_sign_in` |
| REG-5 | Personal data of rejected applicants kept forever | L | Deleted 30 days after the decision (DB CHECK on what is left) | `test_purge_deletes_unverified_after_a_day_and_rejected_personal_data_after_30_days` |

---

## 10. General web and operational risks

| ID | Risk | Rating | Protection | Test(s) |
|---|---|---|---|---|
| WEB-1 | SQL injection | **H** | ORM bind parameters; `set_config` with parameters; sort allowlists; no SQL built from strings (ruff rule S608) | `test_sort_param_allowlist[route]`, `test_injection_payloads_in_filters_harmless` |
| WEB-2 | Cross-site scripting | **H** | React escaping; no `dangerouslySetInnerHTML` (eslint `react/no-danger`); CSP `default-src 'self'` | `test_csp_header_present`, frontend lint |
| WEB-3 | Clickjacking | M | `frame-ancestors 'none'` | `test_frame_ancestors_none` |
| WEB-4 | Open redirect | M | Relative links only (DB CHECK on notifications); post-login `next` must be relative | `test_login_next_param_relative_only`, `test_notification_link_must_be_relative` |
| WEB-5 | Verbose errors | M | Problem details only; no stack traces | `test_500_response_has_no_internals` |
| WEB-6 | Resource exhaustion | M | Page-size caps; rate limits; upload limits | `test_page_size_capped`, `test_rate_limits_return_429` |
| WEB-7 | Vulnerable dependencies (T8) | M | Pinned versions and lockfiles; `pip-audit` and `npm audit`, run locally (Q73) | Quality-gate step (fails on known high/critical issues) |
| WEB-8 | Secrets or keys left in the project folder | **H** | Key files kept outside the project folder; **gitleaks** scans the whole folder in the quality gate (Q73). No GitHub-based scanning. | Quality-gate step + `test_key_file_path_is_outside_repo` |
| WEB-9 | Lost updates on any editable record (Q70) | M | `version` column on the 19 editable tables (03 §1) | `test_stale_version_update_returns_409[editable_table]`, `test_patch_without_version_rejected` |
| OPS-1 | Backup theft exposes everything | M | Backup repository on a separate disk; pgBackRest repository encryption with its own key (Q65); identity documents are ciphertext inside backups anyway | Restore drill uses the backup key from its offline copy (02 §12) |
| OPS-2 | A data key is lost | M | Keyring copies: key file outside the project + offline USB copy + optional sealed paper copy; documented consequences (03 §5.5.1) | Restore drill restores the keyring from the offline copy; `test_missing_key_id_reported_on_health_page` |
| OPS-3 | Audit log altered by someone with superuser access | M | Append-only privileges + trigger. Hash chain **deferred** (Q74); superuser tampering is an accepted residual risk | `test_audit_logs_reject_update_and_delete` |
| OPS-4 | App starts without, or with a wrong, data key and writes unreadable data | M | Startup check: active key present, 32 bytes, decrypts a known test value | `test_app_refuses_to_start_without_valid_active_key` |

---

## 11. Residual risks (accepted for now)

These remain even with every protection above. They're listed so nobody assumes the software covers them.

1. **The password is the only factor** (D87: no two-step verification, a deliberate choice). A stolen or guessed password is enough to sign in, for the Administrator and the Platform Owner too. Lockout, per-IP limits, the 8-character minimum and the common-password list reduce it; they can't remove it.
2. **The one Administrator is trusted with everything** (D75/D76). The audit log records every action, but nobody inside the organization checks the Administrator.
3. **Self-approved refunds** (D76): with one Administrator, refunds are approved by the person who requested them. They are marked and audited, not prevented.
4. **False attestation or mislabelled uploads**: a full Aadhaar scan uploaded as an "admission document", or attested as masked when it isn't. The database forces identity documents into the restricted, encrypted class but can't read the PDF; there's no automatic content check in v1 (Q66).
5. **No antivirus scanning in v1** (Q45).
6. **A database superuser can alter history.** A tamper-evident hash chain is deferred (Q74).
7. **Local-only operation** (Q1): no off-site backup, and the laptop itself is a single point of failure. Fine for development; revisit before hosting.
8. **Fake UPI submissions** are only caught if the Administrator actually checks the bank statement.
9. **Losing every copy of a data key** makes identity documents unreadable forever (03 §5.5.1). The offline copies exist to prevent this.

---

## 12. Test count summary

| Area | Planned tests (approx.) | Generated per route/table? |
|---|---|---|
| Multi-tenancy | 30 named + every classified route × 5 actions | Yes |
| Authentication | ~35 | No |
| Financial ledger | ~30 | Partly (per table / entry type) |
| Leave | 2 | No |
| Document uploads | ~20 | Yes (category × role) |
| Role permissions | ~12 + every route × user type | Yes |
| Web / operational | ~12 + quality-gate tools | Partly |

---

## 13. Questions from this document

Q65 and Q71–Q74 were decided on 2026-11-07:
- **Q65:** encryption at rest, with key management in 03 §5.5.1.
- **Q71:** NIST rules. These replace an earlier 10-character proposal, which didn't match NIST.
- **Q72:** limits as proposed.
- **Q73:** `pip-audit`, `npm audit` and gitleaks, run locally.
- **Q74:** hash chain deferred.

No new questions.

---

## Appendix A. Test traceability

Written after the independent review (review Should fix 10), which found that many names above don't exist under those names. The names in the tables above were written before the code. This table maps each one that is not a real test name to the test that covers it, or records that none does. `backend/tests/test_docs.py` fails if a name above is neither a real test nor listed here, or if a test named here doesn't exist. **11 names are gaps:** planned and not implemented. They are listed honestly, not hidden.

| Name used above | Covered by | Note |
|---|---|---|
| `test_500_response_has_no_internals` | `test_unhandled_error_is_generic_500` |  |
| `test_access_token_rejected_after_session_revoked` | `test_revoked_session_stops_working_immediately` |  |
| `test_allocation_cannot_exceed_charge` | `test_allocation_cannot_exceed_debit` |  |
| `test_app_refuses_to_start_without_valid_active_key` | `test_keyring_validation` | keyring errors raise ConfigError at startup |
| `test_audit_logs_reject_update_and_delete` | `test_financial_and_history_tables_reject_update_and_delete_for_app` | audit_logs is one of its cases |
| `test_charge_run_idempotent` | `test_charge_run_is_idempotent` |  |
| `test_ciphertext_bound_to_file_record` | `test_wrong_associated_data_fails` | the file ID is part of the associated data |
| `test_concurrent_allocations_respect_limits` | `test_one_student_two_beds_exactly_one_wins` | plus the dbproof race tests |
| `test_cross_tenant_filter_param_returns_404` | `test_cross_tenant_filter_param_returns_404` | added after the review |
| `test_cross_tenant_list_excludes_other_org` | `test_lists_contain_only_own_data` |  |
| `test_cross_tenant_read_returns_404` | `test_cross_tenant_resource_returns_404` |  |
| `test_cross_tenant_write_rejected` | `test_cross_tenant_resource_returns_404`, `test_ids_of_another_organization_in_a_body_are_not_found` | body IDs added after the review |
| `test_csp_header_present` | `test_no_token_is_401_with_headers` | checks the CSP header |
| `test_db_rejects_oversized_file_row` | `test_db_rejects_bad_file_rows` |  |
| `test_duplicate_utr_rejected` | `test_duplicate_pending_utr_rejected`, `test_upi_submission_flow` |  |
| `test_error_bodies_never_mention_other_org` | `test_cross_tenant_resource_returns_404` | every response is scanned for org B's names |
| `test_expired_access_token_rejected` | `test_expired_token`, `test_org_route_requires_valid_access_token` |  |
| `test_file_access_matrix` | `test_identity_document_access_only_administrators`, `test_signed_links` | per category, not one matrix |
| `test_file_content_security_headers` | `test_signed_links` | checks CSP sandbox and nosniff on file content |
| `test_financial_tables_reject_update_and_delete` | `test_financial_and_history_tables_reject_update_and_delete_for_app` |  |
| `test_frame_ancestors_none` | `test_no_token_is_401_with_headers` |  |
| `test_identity_document_download_audited` | **not found (gap)** | not found |
| `test_identity_document_records_key_id` | `test_identity_document_encrypted_at_rest` |  |
| `test_identity_documents_never_exported` | **not found (gap)** | not found (no export includes documents today) |
| `test_injection_payloads_in_filters_harmless` | `test_injection_payloads_in_filters_harmless` | added after the review |
| `test_job_for_org_a_cannot_touch_org_b` | `test_job_for_org_a_cannot_touch_org_b`, `test_app_cannot_enqueue_for_another_org` | added after the review |
| `test_jwt_alg_none_rejected` | `test_forgeries_rejected`, `test_bad_tokens_rejected` |  |
| `test_jwt_wrong_key_rejected` | `test_forgeries_rejected`, `test_audiences_use_different_keys` |  |
| `test_key_file_path_is_outside_repo` | **not found (gap)** | not found |
| `test_late_fee_job_idempotent` | `test_late_fee_flat_once`, `test_late_fee_unique_per_charge_and_day` |  |
| `test_login_next_param_relative_only` | **not found (gap)** | not found |
| `test_login_unknown_slug_generic_error` | `test_login_with_other_org_slug_fails` | partly: another org's slug, not an unknown one |
| `test_login_unknown_user_same_response_as_wrong_password` | `test_unknown_user_same_response_as_wrong_password` |  |
| `test_long_unicode_passphrase_accepted` | `test_no_composition_rules_spaces_and_unicode_allowed`, `test_unicode_normalisation` |  |
| `test_missing_key_id_reported_on_health_page` | `test_key_status_flags_items_under_a_key_missing_from_the_keyring` | a CLI report, not the health page (D62) |
| `test_money_as_json_number_rejected` | `test_money_is_a_string_both_ways` |  |
| `test_money_serialized_as_string` | `test_money_is_a_string_both_ways` |  |
| `test_no_composition_rules_imposed` | `test_no_composition_rules_spaces_and_unicode_allowed` |  |
| `test_no_open_charge_and_unallocated_credit_together` | `test_random_money_operations_keep_every_invariant` |  |
| `test_org_id_in_body_rejected` | `test_client_supplied_org_is_ignored` | ignored rather than rejected |
| `test_org_token_rejected_on_platform_routes` | `test_platform_route_rejects_org_tokens` |  |
| `test_password_blocklist_and_context_words` | `test_blocklist_whole_password_case_insensitive`, `test_context_words` |  |
| `test_password_reset_always_202` | `test_password_reset_flow` |  |
| `test_password_reset_revokes_all_sessions` | `test_password_reset_flow` |  |
| `test_patch_me_rejects_privileged_fields` | **not found (gap)** | not found |
| `test_platform_cannot_read_files_or_documents` | `test_platform_cannot_read_tables_outside_window` |  |
| `test_platform_role_has_no_bypassrls` | `test_app_roles_are_not_owner_superuser_or_bypassrls` |  |
| `test_platform_token_rejected_on_org_routes` | `test_org_route_requires_valid_access_token` | platform tokens are one of its cases |
| `test_portal_routes_have_no_id_params` | `test_portal_routes_have_no_student_id_params` |  |
| `test_rate_limits_return_429` | `test_password_reset_requests_rate_limited` |  |
| `test_read_only_user_cannot_write` | `test_read_only_user_may_only_read` |  |
| `test_refresh_rejects_foreign_origin` | `test_refresh_requires_custom_header_and_origin` |  |
| `test_refresh_requires_custom_header` | `test_refresh_requires_custom_header_and_origin` |  |
| `test_rejected_submission_requires_note` | `test_upi_submission_flow` |  |
| `test_restricted_files_have_no_signed_links` | `test_signed_links` |  |
| `test_reversal_creates_audit_row` | **not found (gap)** | not found as its own test |
| `test_reversal_releases_allocations` | `test_reversal_with_active_allocations_rejected_at_commit` |  |
| `test_reversed_payment_has_all_entries_reversed` | `test_payment_reversal_requires_all_entries_reversed` |  |
| `test_same_email_allowed_in_two_orgs` | **not found (gap)** | not found |
| `test_same_student_code_allowed_in_two_orgs` | **not found (gap)** | not found |
| `test_search_never_returns_other_org` | `test_lists_contain_only_own_data` |  |
| `test_signed_link_bound_to_session` | `test_signed_links`, `test_signed_link_expires` |  |
| `test_signed_link_rejected_for_other_user` | `test_signed_links` |  |
| `test_sort_param_allowlist` | **not found (gap)** | not found |
| `test_stale_version_update_returns_409` | `test_stale_edit_is_409_edit_conflict` |  |
| `test_student_cannot_call_admin_routes` | `test_permission_matrix` |  |
| `test_submission_reviewed_by_other_user` | **not found (gap)** | not found |
| `test_suspended_org_requests_rejected` | `test_suspended_organization`, `test_suspension_stops_open_sessions_at_the_next_request` |  |
| `test_unattached_file_visible_only_to_uploader` | `test_unattached_file_visible_only_to_uploader_and_purged` |  |
| `test_unattached_files_purged` | `test_unattached_file_visible_only_to_uploader_and_purged` |  |
| `test_unknown_fields_rejected` | `test_unknown_route_and_validation_errors_use_problem_format` |  |
| `test_unknown_user_still_runs_password_hash` | `test_unknown_user_same_response_as_wrong_password` |  |
| `test_waiver_cannot_exceed_target_outstanding` | `test_waiver_targets_its_charge` |  |
| `test_waiver_requires_permission` | `test_permission_matrix` |  |
| `test_waiver_requires_reason` | **not found (gap)** | not found as its own test (the API requires reason_code and reason_note) |
| `test_waiver_requires_target_charge` | `test_waiver_targets_its_charge` |  |
| `test_worker_claim_policy_only_on_jobs` | `test_worker_cross_org_policy_only_on_jobs` |  |
| `test_worker_sets_job_org_context` | `test_worker_can_claim_jobs_of_every_org` |  |
