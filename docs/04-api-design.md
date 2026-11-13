# 04 — API Design

| | |
|---|---|
| Status | Builds on decisions Q1–Q74 (the project's decision log), [02-architecture-overview.md](02-architecture-overview.md) and [03-database-design.md](03-database-design.md). Items marked **PROPOSAL** are mine and are listed in §14. |
| Scope | Endpoint groups, the full authentication flow, tenant-context handling, errors, pagination, filtering, files and versioning. No code. Exact request and response schemas will come from the Pydantic models during implementation; the JSON below is illustrative. |
| Date | 2026-11-07 |

> **This document describes the current API** (12 November 2026): sign-in is the email (or ID) and the password for everyone, the Platform Owner included (D87). The Administrator's home calls `GET /admin/overview`. Email goes through Gmail when configured, Mailpit otherwise (D88). the project's decision log is the decision record.

---

## 1. Conventions

| Topic | Convention |
|---|---|
| Base URL | Same origin as the web app, under `/api/v1`. In development, Vite proxies `/api` to FastAPI. |
| Format | JSON (UTF-8). `multipart/form-data` only for file uploads. |
| Field names | `snake_case`, the same names as Python and the database (DECIDED Q68). |
| IDs | UUID strings. |
| Timestamps | ISO 8601 in UTC with `Z`, e.g. `"2026-11-07T15:04:05Z"`. The UI converts to the organization's timezone (Q48). |
| Dates | `"YYYY-MM-DD"`. Dates that mean a local day (leave date, due date, check-in date) are in the organization's timezone. |
| Money | **Decimal strings** with two places, e.g. `"1250.00"`, never JSON numbers (JavaScript numbers are floats). A JSON number in a money field is rejected with 422. |
| Enumerations | Lower-case strings matching the database values (`"pending"`, `"upi"`). |
| Unknown request fields | Rejected with 422 (Pydantic `extra="forbid"`). This blocks "mass assignment", e.g. sending `"role": "super_admin"` in a profile update. |
| Methods | `GET` reads; `POST` creates or performs an action; `PATCH` partially updates. No `PUT`. Nothing is deleted through the API; "deactivate", "cancel" and "reverse" are POST actions on the resource (`POST /leave-requests/{id}/cancel`). |
| Request ID | Every response carries `X-Request-ID`; error bodies repeat it. |

## 2. Audiences and URL layout

| Prefix | Who calls it | Authentication | Database role | Tenant context comes from |
|---|---|---|---|---|
| `/api/v1/health` | Health checks | none | `hms_app` | — |
| `/api/v1/public` | Anyone | none | `hms_app` | none, or the slug in the path |
| `/api/v1/o/{org_slug}/auth` | Anyone, before login to that organization | none | `hms_app` | **the slug** (resolved server-side) |
| `/api/v1/auth` | A user signing in by email, refreshing, logging out or managing sessions | refresh cookie or access token | `hms_app` | token or cookie |
| `/api/v1/me` | Any logged-in organization user (staff or student) | access token | `hms_app` | token |
| `/api/v1/admin` | Staff: the Administrator (D75/D76) | access token, `kind = staff` | `hms_app` | token |
| `/api/v1/portal` | Students | access token, `kind = student` | `hms_app` | token |
| `/api/v1/files` | Any logged-in organization user | access token or signed link | `hms_app` | token or signed link |
| `/api/v1/platform` | Platform Owner | platform access token | `hms_platform` | none (no tenant) |

**Rule: authenticated URLs never contain the organization slug or `org_id`.** The token decides the tenant. A slug in an authenticated URL would be redundant at best, and at worst a value someone is tempted to trust. Only pre-login URLs carry the slug.

Each prefix is its own FastAPI router with its own authentication dependency. So "a student calling a staff endpoint" fails at the router level before any business logic runs.

## 3. Versioning

- **Major version in the path:** `/api/v1`. Within v1, changes are additive only: new endpoints, new optional request fields, new response fields, new enum values (the frontend must tolerate unknown values).
- **Breaking changes** (renaming or removing a field, changing a meaning) go into `/api/v2`, served alongside v1 during a transition period. The frontend and backend ship together from one repository, so a v2 is unlikely before outside clients exist.
- **Deprecation:** endpoints scheduled for removal send `Deprecation` and `Sunset` response headers.
- **Contract:** FastAPI publishes the OpenAPI document at `/api/v1/openapi.json`, with interactive docs at `/api/v1/docs` in development. PROPOSAL: docs are switched off when hosted. The frontend's TypeScript types are generated from that document (02 §3), so a backend change that breaks the contract becomes a frontend compile error.

## 4. Tenant context: how every request gets it

### 4.1 Authenticated requests (`/me`, `/admin`, `/portal`, `/files`, most of `/auth`)

```
Request ──► middleware: request ID, security headers
        ──► get_principal:  verify JWT (alg pinned, key by kid, iss, aud="hms-api", typ="access", exp)
                            → user_id (sub), org_id (org), session_id (sid)
        ──► get_db:         new SQLAlchemy session on the hms_app engine
                            BEGIN
                            SELECT set_config('app.org_id', :org_id, true)    ◄── RLS is now scoped
        ──► load_context:   one query (under RLS): session not revoked/expired and belongs to sub,
                            user active (read_only → GET only),
                            organization status = active; then role assignments
                            → Principal(user_id, org_id, kind, roles[(role, hostel_id)], allowed_hostels)
        ──► require(perm):  route-level permission check (§6)
        ──► handler/service: business rules + hostel/self scope; audit + jobs in the same transaction
        ──► COMMIT (or ROLLBACK on any error) → the setting disappears with the transaction
```

Why it's built this way:

- **The tenant comes from the verified token only.** Never from a header, query parameter, body field or URL (Layer 1 in 02 §8).
- **`set_config(..., true)` runs at the very start of the request's transaction**, inside the database-session dependency. Every query in the request then runs inside that transaction. Middleware can't do this because it doesn't own the database session. Rule: **no code creates its own database session**; everything uses the dependency. A test enforces this (05 MT-5).
- **`set_config` instead of `SET LOCAL`:** both are transaction-local, but `SET LOCAL` can't take a bind parameter, so the UUID would have to be pasted into the SQL string. `set_config('app.org_id', :org_id, true)` is parameterized, so there's no SQL-injection path even in principle.
- **Connection pooling is safe.** The setting is local to the transaction, so it is gone when the connection goes back to the pool. SQLAlchemy's pool also resets connections on return.
- **Fail closed.** If a code path ever skipped the dependency, `app.org_id` would be unset and RLS would return zero rows, so the bug shows up as "not found", not as a leak.
- **The organization's status is checked on every request** (Q14). A suspended organization's tokens stop working immediately: `403 organization_suspended`.
- **Read-only users** (exited students within their 90-day window, Q22) may only use `GET`.

### 4.2 Pre-login requests (`/o/{org_slug}/auth/*`)

1. Look up the slug: `SELECT id, status FROM organizations WHERE slug = :slug`. This works without tenant context because `organizations` is readable by everyone (03 §6). Slugs are not secret; the login page shows the organization's name.
2. If the organization is missing or not `active` → generic error (§7).
3. `set_config('app.org_id', <that id>, true)`. From here on, user lookups (Student ID, Employee ID, email) only see that organization's rows. One organization's credentials can't log in at another's URL.

### 4.3 Refresh requests

The refresh cookie is `hms_refresh = <org_id>.<session_id>.<secret>`, with `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`. The server reads the org ID, sets the tenant context, loads the session row, and compares the hash of `<secret>` with `refresh_token_hash` in constant time. The org ID in the cookie is untrusted input, but harmless: without the matching secret it achieves nothing.

### 4.4 Platform requests (`/platform/*`)

- A separate SQLAlchemy engine connects as **`hms_platform`** and never sets `app.org_id`. What it can read is decided by the database: explicit policies plus column grants (03 §6).
- Platform tokens have `aud = "hms-platform"` and are signed with a **different key** from organization tokens (PROPOSAL). An organization token is rejected on platform routes, and the reverse.

### 4.5 Background jobs

For each job the worker runs BEGIN → `set_config('app.org_id', <job.org_id>, true)` → the job → COMMIT. Platform-wide jobs (`org_id` NULL) run without context and can only touch platform data.

## 5. Authentication flows

### 5.1 Endpoints at a glance

| Method | Path | Purpose |
|---|---|---|
| GET | `/public/organizations/{org_slug}` | Name and status for the login page |
| GET | `/public/slug-availability?slug=` | During registration (rate-limited) |
| POST | `/public/organizations` | Self-registration (§5.2) |
| POST | `/o/{org_slug}/auth/verify-registration` | Email verification for a new organization |
| POST | `/auth/login` | D78: Administrator email + password, no organization in the address (§5.3) |
| POST | `/auth/password-reset/request`, `/auth/password-reset/confirm` | D78: forgot password with the Administrator's email only |
| POST | `/o/{org_slug}/auth/login` | Password step (§5.3) |
| POST | `/o/{org_slug}/auth/activate/preview` | Check an activation link before showing the form |
| POST | `/o/{org_slug}/auth/activate` | Set the first password (§5.7) |
| POST | `/o/{org_slug}/auth/password-reset/request` | Always 202; emails a 6-digit code (D77) |
| POST | `/o/{org_slug}/auth/password-reset/confirm` | Code + new password; revokes all sessions |
| POST | `/auth/refresh` | Rotate the refresh token, get a new access token |
| POST | `/auth/logout` | Revoke this session |
| GET | `/auth/sessions` | List my sessions (device, last used) |
| POST | `/auth/sessions/{session_id}/revoke` | Revoke one |
| POST | `/auth/sessions/revoke-others` | "Log out everywhere else" |
| POST | `/auth/password/change` | Needs the current password; revokes other sessions |
| POST | `/platform/auth/login`, `/platform/auth/refresh`, `/platform/auth/logout` | Platform Owner equivalents (§5.9) |
| GET | `/platform/me` | The signed-in Platform Owner (Phase 1 addition) |

### 5.2 Organization self-registration

```
1. GET  /public/slug-availability?slug=sunrise-hostels        → {"available": true}
2. POST /public/organizations
        {"organization": {"name": "...", "slug": "sunrise-hostels", "contact_email": "..."},
         "super_admin":  {"full_name": "...", "email": "...", "password": "..."}}
   → 202 Accepted
   Creates (one transaction): organization (pending_verification) + profile
     + Administrator user (password set, pending_activation) + super_admin role
     + auth_token (purpose org_registration) + email job → Mailpit
3. The emailed link opens  /o/sunrise-hostels/verify#token=...
   (the token is in the URL fragment, which browsers never send to servers or in Referer headers)
4. POST /o/sunrise-hostels/auth/verify-registration {"token": "..."}
   → organization active, Administrator active and email verified
   → 200 {"status": "authenticated", "access_token": "..."} + refresh cookie
```

A slug taken in a race returns `409 slug_taken`. Registration is rate-limited per IP (Q72).

### 5.3 Login (organization users)

**D80 (12 November): Student sign-in too:** the same `POST /auth/login` with `"portal": "student"` finds the organization through `student_org(email)` (student emails are unique across the platform; a registration request's email counts for the D74 messages), and the reset routes take `"portal"` as well. **D78 (11 November): Organization sign-in by email only.** `POST /auth/login {"email", "password"}` finds the organization through the database function `administrator_org(email)` (Administrator emails are unique across the platform) and then behaves exactly like the staff login below: same responses, same `401 invalid_credentials` for an unknown email, a wrong password, a student's email or an unverified organization, same limits. Forgot password with the email only: `POST /auth/password-reset/request {"email"}` and `POST /auth/password-reset/confirm {"email", "code", "new_password"}` (§5.8). The organization-specific route below stays for `/o/{name}/login` and Student sign-in.

```
POST /o/{org_slug}/auth/login
{"portal": "staff" | "student", "identifier": "STU-2026-0042" | "name@example.com", "password": "..."}
```

- The identifier is treated as an email if it contains `@`. Otherwise it's an Employee ID (`portal = staff`) or a Student ID (`portal = student`), upper-cased.
- **On correct password**, a `sessions` row is created and the person is signed in (D87: no second step):

| Response | When | Body |
|---|---|---|
| `200` authenticated | Correct password, active account and organization | `{"status": "authenticated", "access_token": "...", "expires_in": 900, "user": {...}}` + `Set-Cookie: hms_refresh=...` |
| `401 invalid_credentials` | Unknown identifier, wrong password, or wrong portal | Same message in all cases. For unknown identifiers the server still runs a dummy Argon2 check, so response time doesn't reveal whether the account exists. |
| `429 too_many_attempts` | Account lock or IP throttle (Q72) | `Retry-After` header; same body whichever limit was hit |
| `403 account_deactivated` / `403 organization_suspended` | Only after a **correct** password | Lets a real user understand the problem without revealing anything to a guesser |


### 5.6 Tokens and sessions

| Token | Form | Lifetime (DECIDED Q67) | Stored where |
|---|---|---|---|
| Access token | JWT, HS256 (PROPOSAL; one service, so a shared secret is enough), `kid` header for key rotation. Claims: `iss`, `aud`, `sub`, `org`, `sid`, `typ = "access"`, `iat`, `exp`, `jti`. **No roles, names or emails** (tokens can end up in logs) | 15 min | Browser memory only |
| Refresh token | Opaque `<org_id>.<session_id>.<secret>` | 7 days sliding, 30 days absolute; Platform Owner 8 h absolute | `HttpOnly; Secure; SameSite=Strict` cookie; hash in `sessions` |

- **Refresh rotation:** each `POST /auth/refresh` revokes the presented session row (`rotated`) and creates a new one in the same `family_id`, returning a new access token and a new cookie.
- **Reuse detection:** if an already-rotated token is presented **more than 10 s after rotation**, it was probably stolen, so the whole family is revoked (`reuse_detected`) and the user must log in again.
- **Two-tab race (PROPOSAL):** two open tabs may refresh at the same moment. Within the 10-second grace window the server answers `401 refresh_superseded` *without* revoking anything; that tab retries, by which time the browser already holds the newer cookie (cookies are shared between tabs). The frontend also serializes refreshes across tabs with the browser's Web Locks API.
- **CSRF:** only `/auth/refresh` and `/auth/logout` use the cookie. They also require the header `X-Requested-With: hms` and an `Origin` matching the site. Every other endpoint uses the `Authorization` header, which browsers never attach automatically.

### 5.7 Activation links

The link is only ever **emailed** (D80).

- Sent when a student is admitted (an email is required, D80) or approved from a registration request (D74). `POST /admin/users/{user_id}/activation-email` (SA) sends it again: `202`, or `409 already_activated`.
- Issuing a new link revokes the previous live one (database: one live token per user and purpose). Stored as a hash only, `delivery = 'email'`; 7 days.
- The token travels in the URL **fragment** (`#token=`), so it never reaches server logs or Referer headers.
- The activation page calls `POST /o/{slug}/auth/activate/preview {"token"}` (returns the account's display name and expiry, or `410 link_expired`), then `POST /o/{slug}/auth/activate {"token", "password"}` → `204`. The account becomes active; the person then signs in (no automatic login).
- Audit records `activation_link.issued` and `account.activated`, never the token.

### 5.8 Password reset

**Changed by D77 (11 November): an emailed 6-digit code, no link.**

- `POST /o/{slug}/auth/password-reset/request {"identifier"}` (email, Employee ID or Student ID) → **always `202`**, whether or not the account exists. If it exists, is active or read-only and has an email, the worker emails a **6-digit code**, valid **10 minutes**, single use; a new code cancels the previous one. The subject doesn't contain the code.
- `POST /o/{slug}/auth/password-reset/confirm {"identifier", "code", "new_password"}` → `204`. All sessions are revoked, the lockout is cleared, `user.password_reset` is audited and the user is notified.
  - A wrong, expired, used or cancelled code, an unknown account and an unknown organization all answer `400 invalid_reset_code`, after the same Argon2 work. **5 wrong codes cancel the code.** 20 wrong codes per IP in 15 minutes → `429`.
  - A weak new password → `422 validation_error`, and the code stays usable.
- The web app: `/forgot-password` (public) and `/o/{slug}/forgot-password` (organization filled in), one page in two steps. The `/o/{slug}/reset-password` link page is gone.
- **Users without email:** none any more. Every student has an email (D80), and the Administrator's email is required at registration. The in-person reactivation of Q69 was removed (D80).

### 5.9 Platform Owner authentication

Same shape under `/platform/auth/*` (email and password only, D87), with: a separate cookie `hms_platform_refresh` on path `/api/v1/platform/auth`; `aud = "hms-platform"` and a separate signing key; shorter lifetimes (Q67); and a database connection as `hms_platform` (§4.4).

### 5.10 Rate limits (DECIDED Q72)

| What | Limit |
|---|---|
| Wrong password, per account | 5 → account locked 15 min |
| Failed logins, per IP | 20 per 15 min → throttled |
| Password-reset requests | 3 per hour per identifier, 20 per hour per IP; per account 1 per minute and 3 per hour (D77) |
| Password-reset codes (D77) | 5 wrong per code → cancelled; 20 wrong per IP per 15 min → throttled |
| Self-registration | 3 per hour per IP |
| Exports (payments CSV) | 10 per hour per user |

Implemented with database counters (`login_attempts`, user lock columns) for authentication, and a small in-process limiter for the rest (single API process for now). `429` responses carry `Retry-After`.

### 5.11 Password rules (DECIDED Q71, following NIST SP 800-63B-4)

Checked on registration, activation, password change and reset:

| Rule | Value | Why |
|---|---|---|
| Minimum length | **8 characters**, for everyone (D87) | A deliberate choice. NIST SP 800-63B-4 asks for 15 when the password is the only factor; the lockout, IP limits and blocklist reduce the risk (05 §11) |
| Maximum length | At least 64 characters accepted | Long passphrases must work |
| Characters | Any printable characters, including spaces and Unicode | No composition rules |
| Blocklist | The whole password is compared with a bundled list of the 100,000 most common/breached passwords, plus context words (organization name and slug, the user's name, email and ID). Checked offline; no external service | NIST requires a blocklist |
| Composition rules ("must contain a symbol") | **None** | NIST says not to impose them |
| Periodic expiry | **None.** Change is forced only on evidence of compromise | NIST says not to require regular changes |

A rejected password returns `422` with a message saying *why* (too short / too common), never a list of rules to satisfy.

## 6. Authorization in the API

> **One role (D75/D76):** the Administrator (`super_admin`), exactly one per organization, has every permission in every hostel. The hostel-scope checks still run but narrow nothing.

- **Route level:** each route declares the permission it needs, e.g. `require("payments.record")`. Roles map to permission sets in code (02 §7). The Administrator has every permission (Q59, D75).
- **Scope level:** services filter every list by the caller's allowed hostels and check each single record. Students are always "me", derived from the token.
- **404 vs 403:**
  - Resource in another organization, another hostel outside your scope, or another student's record → **404 `not_found`**, so nothing about its existence leaks.
  - Resource inside your scope, but your role may not perform the action → **403 `permission_denied`**.
  - Student calling `/admin` or staff calling `/portal` → **403 `wrong_portal`**, at the router.
- **Second-person rules** (refund approval) are enforced by the database; violations return **403 `second_person_required`**. The sole Administrator's own approval is the allowed, marked exception (§12.9, D76).
- **What each side sees:** the Administrator gets the full student record; a student sees only their own data through `/portal/me/...`.

## 7. Errors

All errors use **RFC 9457 "problem details"** (`Content-Type: application/problem+json`):

```json
{
  "type": "/problems/bed_already_allocated",
  "title": "Bed already allocated",
  "status": 409,
  "code": "bed_already_allocated",
  "detail": "This bed is occupied or reserved for overlapping dates.",
  "instance": "/api/v1/admin/allocations",
  "request_id": "0193f2a4-7c1e-7b2a-9d1e-4f0c2b7a9e11",
  "errors": []
}
```

Validation errors (`422`) list each problem:

```json
{ "type": "/problems/validation_error", "title": "Invalid request", "status": 422, "code": "validation_error",
  "request_id": "…",
  "errors": [ { "field": "amount", "code": "decimal_string_required", "message": "Send money as a string like \"1250.00\"." },
              { "field": "received_on", "code": "date_in_future", "message": "Payment date can't be in the future." } ] }
```

| Status | Used for |
|---|---|
| 400 | Malformed request (bad JSON, bad header), `invalid_cursor`, `invalid_reset_code` (D77) |
| 401 | `token_expired`, `session_invalid`, `invalid_credentials`, `refresh_superseded` |
| 403 | `csrf_failed` (cookie endpoints), `permission_denied`, `wrong_portal`, `organization_suspended`, `account_deactivated`, `account_read_only` (a read-only user tried to change something, Q22), `second_person_required`, `registration_unverified`, `registration_pending`, `registration_rejected` (D74, sign-in only, after the correct password) |
| 404 | `not_found` (including other organization, hostel or student) |
| 409 | `admin_email_taken` (D78: registration with an email an Administrator already uses); conflicts with current state (table below); including `edit_conflict` (Q70) and `retry_conflict` (database deadlock/serialization failure; body has `"retryable": true`, header `Retry-After: 1`; 03 §8) |
| 410 | `link_expired` (activation and registration links) |
| 413 / 415 | `file_too_large` / `unsupported_file_type` |
| 422 | `validation_error`, `idempotency_key_reused`, `password_too_short_for_single_factor` |
| 429 | `too_many_attempts` / `rate_limited` (with `Retry-After`) |
| 500 | `internal_error`: generic message + `request_id`, nothing else |
| 503 | `not_ready` (`/health/ready` only: database unreachable or schema older than the code) |

**Named database constraints map to error codes**, so the database's guarantees surface as clear messages instead of 500s:

| Constraint / trigger (03) | HTTP | `code` |
|---|---|---|
| `bed_no_overlap` | 409 | `bed_already_allocated` |
| `student_no_overlap` | 409 | `student_already_allocated` |
| `leave_no_overlap` | 409 | `leave_overlaps_existing` |
| `payments_reference_uq` | 409 | `duplicate_payment_reference` |
| `payment_submissions_utr_uq` | 409 | `utr_already_submitted` |
| `ledger_guard_deposit_floor` | 409 | `insufficient_deposit` |
| `ledger_guard_refund_exceeds_credit` | 409 | `refund_exceeds_credit` |
| `ledger_guard_refund_not_approved` | 409 | `refund_not_approved` |
| `ledger_allocations_exceeds_balance` | 409 | `allocation_exceeds_balance` |
| `ledger_allocations_guard` | 409 | `invalid_allocation` |
| `refund_requests` approver CHECK | 403 | `second_person_required` |
| `users_org_email_uq` | 409 | `email_in_use` (only ever about your own organization) |
| `version` compare-and-increment matched 0 rows | 409 | `edit_conflict` |
| SQLSTATE `40P01` / `40001` (any statement) | 409 | `retry_conflict`, retryable; never a 500 |
| `ledger_waiver_fully_allocated` / waiver over outstanding | 409 | `waiver_exceeds_outstanding` |

**Never in an error body:** SQL text, stack traces, other organizations' data, or whether a resource exists outside your scope. The full exception is logged server-side under the `request_id`, with secrets redacted.

## 8. Pagination

Two styles, chosen per endpoint:

| Style | Used for | Request | Response |
|---|---|---|---|
| **Page-based** | Admin tables of bounded size per organization: students, staff, rooms, complaints, fee structures, payments | `?page=1&page_size=25` (max 100) | `{"items": [...], "page": {"number": 1, "size": 25, "total_items": 4210, "total_pages": 169}}` |
| **Cursor-based** | Append-only or high-volume feeds: ledger entries, audit logs, notifications | `?limit=50&cursor=...` (max 200) | `{"items": [...], "next_cursor": "..." \| null}` |

- Cursors are opaque base64url strings encoding the last row's sort key and `id`, used for **keyset pagination** (`WHERE (created_at, id) < (:t, :id)`). They stay correct when new rows arrive and stay fast deep into the list. A cursor carries no tenant information, and RLS applies anyway, so a tampered cursor can't reach another organization.
- Every list has a fixed default sort with `id` as the final tie-breaker, so pages never overlap or skip.
- Page-based totals cost one `COUNT(*)`, which is cheap at the target scale (Q3: ≤ 5,000 students per organization).

## 9. Filtering, sorting and search

- **Explicit filter parameters per endpoint**, documented in OpenAPI. Unknown parameters → 422.
  - Exact match: `?status=pending&hostel_id=...`
  - Several values: `?status=open&status=in_progress`
  - Date ranges: `?from=2026-08-01&to=2026-08-31`, inclusive, in the organization's timezone
- **Sorting:** `?sort=created_at` or `?sort=-created_at`, only from a per-endpoint allowlist. The server maps the allowed names to columns itself, so user input never reaches an `ORDER BY` clause.
- **Search:** `?q=` (2–50 characters), a case-insensitive substring match on a few fields per endpoint (name, Student ID, room number), with `%` and `_` escaped. At ≤ 5,000 students per organization this needs no extension; `pg_trgm` only if it ever gets slow, with approval.
- **Scope first:** filters can only narrow what the caller may see. A `hostel_id` outside the caller's scope returns `404 not_found`, never an empty-but-revealing result.
- **Exports:** `GET .../export.csv` takes the same filters and streams the CSV (Q47). Exports are rate-limited and audited (`payments.exported`, with the filters used). **Identity documents and ID numbers are never in exports** (Q62).

## 10. Idempotency and concurrent edits

- **`Idempotency-Key` header** (a UUID generated by the browser) is **required on `POST /admin/payments`** and stored in `payments.idempotency_key` (03). The same key with the same body returns the original result; the same key with a different body → `422 idempotency_key_reused`. Other creates are protected by natural database keys (UTR, overlapping leave or allocation, one refund per request).
- **Edit conflicts (DECIDED Q70):** every editable record carries a `version` number (03 §1). `GET` returns it; every `PATCH` must send it back:

  ```
  PATCH /admin/students/{id}   {"version": 4, "phone": "+91 98765 43210"}
  → 200 {..., "version": 5}
  → 409 {"code": "edit_conflict", "title": "Someone else changed this record",
         "detail": "It was updated after you opened it. Reload to see the latest version, then reapply your change.",
         "current_version": 6, ...}
  ```

  The server runs `UPDATE … WHERE id = :id AND version = :version` and increments the version. Zero rows means someone saved first. A `PATCH` without `version` → 422. The UI shows the message and offers "Reload".

## 11. Files: upload and download

### 11.1 Upload (two steps)

```
1. POST /api/v1/files     multipart: category=<category>, file=<binary>
   → 201 {"file_id": "...", "category": "identity_document", "mime_type": "application/pdf",
          "size_bytes": 182344, "sha256": "..."}
2. Reference file_id when creating the owning record, e.g.
   POST /admin/students/{id}/documents
   {"file_id": "...", "document_type": "identity", "id_type": "aadhaar",
    "id_number_masked": "XXXXXXXX1234", "masked_aadhaar_attested": true}
```

- **Step 1 runs the upload pipeline** (02 §9): size enforced while streaming (`413`), real type detected from the file's content (`415`), per-category allowlist, images re-encoded, random storage name. The file is **unattached**: only its uploader can see it, and if no record references it within 24 hours a job purges it (PROPOSAL).
- **Who may upload which category:**

| Category | Uploaded by |
|---|---|
| `student_photo`, `admission_document`, `identity_document` | The Administrator |
| `payment_proof` | The student (with a UPI submission, Q28) |
| `complaint_attachment` | The student (with a complaint) |

- **Aadhaar (Q62):** `id_type = "aadhaar"` requires a PDF in the `identity_document` category, a masked number like `XXXXXXXX1234`, and `masked_aadhaar_attested: true` ("I confirm this is the masked Aadhaar PDF downloaded from UIDAI"). The API returns 422 otherwise, and the database CHECKs are the backstop (03 §5.6). The UI explains how to download the masked Aadhaar PDF from UIDAI and that it is password-protected.
- **Real-file checks (Q66):** images must decode and are re-encoded; a PDF must have a `%PDF-` header and an `%%EOF` trailer. A file that only *claims* to be a PDF → `415 unsupported_file_type`. No content inspection inside PDFs in v1.
- **Encryption (Q65):** `identity_document` files are encrypted with the active data key **before** they touch the disk, and the key ID is stored with the file (03 §5.5.1).
- Limits (PROPOSAL): 10 MB per file, 5 MB for photos.

### 11.2 Download

**Why there are two mechanisms:** the access token lives in JavaScript memory, so it can be sent on `fetch()` calls but not on an `<img src="...">` tag, which is how photos display.

| File class | How it's delivered |
|---|---|
| **Standard** (photos, admission documents, payment proofs, complaint attachments) | `GET /files/{file_id}/link` checks permission through the owning record and returns `{"url": "/api/v1/files/{file_id}/content?exp=...&sig=...", "expires_at": ...}`. The link is valid **60 seconds** and is signed (HMAC) over file ID, user ID, session ID and expiry. The content endpoint re-checks the signature *and* that the session is still active. |
| **Restricted** (`identity_document`, Q62) | **No links.** Only `GET /admin/students/{student_id}/documents/{document_id}/content` with the `Authorization` header. Allowed for the Administrator only. The server decrypts it in memory (never to a temporary file) and streams it; the frontend fetches it as a blob and opens it in a viewer. **Every download writes an audit record** (`student_document.downloaded`). |

Response headers on all file content:
- `Content-Type`: the stored, verified type
- `Content-Disposition: attachment; filename*=UTF-8''<sanitized original name>` (`inline` only for images)
- `X-Content-Type-Options: nosniff`
- `Content-Security-Policy: sandbox`
- `Cache-Control: private, no-store`
- `Referrer-Policy: no-referrer`

## 12. Endpoint groups by module

Paths are relative to `/api/v1`. Who: **SA** the organization's Administrator (role `super_admin`, exactly one per organization, D75/D76), **ST** Student, **PO** Platform Owner; **staff** = SA.

### 12.1 Health and public

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/health/live` | anyone | Process is up |
| GET | `/health/ready` | localhost only | Database reachable, migrations current |
| GET | `/public/organizations/{org_slug}` | anyone | Name and status for the login page |
| GET | `/public/slug-availability` | anyone | Rate-limited |
| POST | `/public/organizations` | anyone | Self-registration (§5.2) |

### 12.2 Authentication

See §5.1.

### 12.3 Me (staff and students)

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/me` | any | Profile, roles, hostels, and the caller's permission list (for showing/hiding UI) |
| PATCH | `/me` | any | Students: phone and emergency contact only (Q46); staff: phone |
| GET | `/me/notifications` | any | Cursor |
| GET | `/me/notifications/unread-count` | any | The true unread count, never a page size; polled every 60 s. The bell shows it, "99+" above 99 (D84) |
| POST | `/me/notifications/{id}/read`, `/me/notifications/read-all` | any | The bell's list: clicking a notification marks it read and opens its `link`; "Mark all as read". A `link` is one of the pages that exist or null (D84) |
| GET, PATCH | `/me/notification-preferences` | any | Mute announcement/reminder emails (Q49) |

### 12.4 Organization and academic calendar

| Method | Path | Who | Notes |
|---|---|---|---|
| GET, PATCH | `/admin/organization` | SA | Name, timezone, contact details, UPI ID (Q28) |
| GET, POST | `/admin/academic-sessions` | SA | |
| PATCH | `/admin/academic-sessions/{id}` | SA | |
| POST | `/admin/academic-sessions/{id}/make-current` | SA | |
| GET, POST | `/admin/academic-sessions/{id}/terms` | SA | Needed for term billing |

### 12.5 Hostel structure

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/admin/hostels` | staff | Only hostels in scope |
| POST | `/admin/hostels` | SA | |
| GET, PATCH | `/admin/hostels/{id}` | SA | Settings: resident type, outing approval (Q35) |
| GET, POST | `/admin/hostels/{id}/buildings`, `/admin/buildings/{id}/floors`, `/admin/floors/{id}/rooms`, `/admin/rooms/{id}/beds` | SA | |
| GET, POST, PATCH | `/admin/hostels/{id}/room-types` | SA | |
| PATCH | `/admin/rooms/{id}`, `/admin/beds/{id}` | SA | Maintenance / inactive status |
| GET | `/admin/hostels/{id}/bed-board` | SA | Live bed states (03 §9) |
| POST | `/admin/hostels/{id}/imports/rooms-beds` | SA | CSV (Q23), multipart; validated and applied in one transaction, row errors in the 422 body (D11) |

### 12.6 The Administrator (D75, D76)

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/admin/staff` | SA | The organization's Administrator, plus staff accounts closed by D75/D76 (read-only) |
| GET, PATCH | `/admin/staff/{id}` | SA | Edit details (name, email, phone, designation); never status or role |

**Removed (D75/D76):** `POST /admin/staff` (adding staff), `/admin/staff/{id}/deactivate`, `/admin/staff/{id}/reactivate`, `/admin/staff/{id}/roles`, `/admin/role-assignments/{id}/revoke`. The Administrator is created by organization registration (§5.2); the database allows no second one (409 `one_administrator_only`).

### 12.7 Students

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/admin/students` | SA | Filters: hostel, status, `q`, `without_bed` (the bed map's student picker) |
| POST | `/admin/students` | SA | Admission: details, **email (required, unique across the platform; 409 `student_email_taken`, D80)**, guardians, `photo_file_id` (mandatory, Q39); generates Student ID; sends the activation email |
| POST | `/admin/users/{id}/activation-email` | SA | D80: send a student's activation email again (202); the new link replaces the old. 409 `already_activated`. |
| GET, PATCH | `/admin/students/{id}` | SA | |
| GET, POST | `/admin/students/{id}/guardians` | SA | Emergency contacts |
| PATCH | `/admin/students/{id}/guardians/{guardian_id}` | SA | With `version` |
| GET, POST | `/admin/students/{id}/documents` | SA | Lists type, masked number and status; Aadhaar rules (§11.1) |
| GET | `/admin/students/{id}/documents/{document_id}/content` | SA | Restricted file; audited (§11.2) |
| POST | `/admin/students/{id}/documents/{document_id}/verify` | SA | |
| POST | `/admin/students/{id}/exit` | SA | After check-out (409 `allocation_open` otherwise). Money never blocks it (Q93): with dues or a held deposit it answers 409 `outstanding_balance` with `dues` and `deposit_held`; resend with `outstanding_note` (5–500 characters) to record the exit. The amounts and note are audited; settlement stays on the Fees card (Q22) |
| GET | `/admin/students/{id}/allocations` | staff | Allocation history |
| GET | `/admin/students/export.csv` | SA | Audited; no ID documents or numbers |

### 12.7a Student registration requests (D74)

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/public/organizations/{slug}/student-registration` | public | `accepts_requests`, organization name, active hostels. An unknown organization answers like one with the switch off |
| POST | `/public/organizations/{slug}/student-registration` | public | Multipart: hostel, name, email, phone, gender, date of birth, programme, year, guardian name/relationship/phone, password, `photo`, honeypot `website`. **Always 202** for a valid form (a known email is told by email instead). 404 `not_accepting_requests`; 422 for fields, weak password, `gender_hostel_mismatch`; 413/415 for the photo; 429 (5/hour per IP, 50/day per organization) |
| POST | `/o/{slug}/auth/student-registration/verify` | public | `{"token"}` from the emailed link (24 hours) → 204; the request becomes `pending` and the Administrator is notified. 410 `link_expired` |
| GET | `/admin/registration-requests?status=pending\|approved\|rejected` | SA | Paged; pending oldest first |
| GET | `/admin/registration-requests/{id}` | SA | Details (no password hash); `has_photo` |
| GET | `/admin/registration-requests/{id}/photo` | SA | The photo the student sent (JPEG), until approval |
| POST | `/admin/registration-requests/{id}/approve` | SA | The office's admission code: user (request's password, active), student with a Student ID, guardian, photo file. → `{student_id, student_code}`. 409 `request_already_decided`, `no_current_session` |
| POST | `/admin/registration-requests/{id}/reject` | SA | `{"reason"}` 5–500 characters. 409 `request_already_decided` |

`PATCH /admin/organization` takes `accepts_student_requests` (off by default). **Sign-in (§5.3), student portal, an email with no account:** if the email belongs to a registration request and the password is the request's, the answer is `403 registration_unverified`, `registration_pending` or `registration_rejected` (the detail carries the administrator's reason); a wrong password is the usual `401 invalid_credentials`. There is no status endpoint.

### 12.8 Bed allocation

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/admin/allocations` | staff | Filters: hostel, status, dates |
| POST | `/admin/allocations` | SA | `mode` = `assign` (checked in now) or `reserve`; `student_id`, `bed_id`, optional `start_date`, `end_date`, `reservation_expires_at` (default 7 days, D23); 409 on overlap; 409 `bed_unavailable` for maintenance/inactive; 422 `different_hostel` (D27) |
| POST | `/admin/allocations/{id}/check-in`, `/check-out`, `/cancel` | SA | Same-day check-out records a cancelled zero-length stay (D24) |
| POST | `/admin/allocations/{id}/transfer` | SA | `{bed_id}`; ends the stay and starts the new one in one transaction, both beds locked in ascending ID order |
| GET | `/admin/beds/{id}/allocations` | staff | Bed history (ALC-3) |

### 12.9 Fees, payments and the ledger

| Method | Path | Who | Notes |
|---|---|---|---|
| GET, POST | `/admin/fee-structures` | SA | |
| PATCH | `/admin/fee-structures/{id}` | SA | Amount locked once charges exist |
| POST | `/admin/charge-runs` | SA | Generate charges for a period; runs as a job; safe to rerun; `202 {job_id, status}` |
| GET | `/admin/charge-runs/{job_id}` | SA | Status of the run |
| GET | `/admin/students/{id}/balance` | SA | Dues, deposit held, deposit due, open charges with outstanding amounts |
| GET | `/admin/students/{id}/ledger` | SA | Newest first, `limit`/`before`; entries with their allocations (D29) |
| POST | `/admin/students/{id}/charges` | SA | Manual charge or fine (reason required for fines) |
| POST | `/admin/students/{id}/waivers` | SA | `target_entry_id` (the charge being waived) required (Q64); amount ≤ that charge's outstanding; reason code and note required |
| POST | `/admin/ledger-entries/{id}/reverse` | SA | Reason required; releases allocations |
| POST | `/admin/payments` | SA | **`Idempotency-Key` required.** Body: `student_id`, `method`, `reference`, `received_on`, `dues_amount`, `deposit_amount` (strings) |
| GET | `/admin/payments`, `/admin/payments/{id}` | SA | |
| GET | `/admin/payments/export.csv` | SA | Cash book with a `reversed` column; audited; 10 per hour per user |
| GET | `/admin/payments/{id}/receipt.pdf` | SA | Anyone with `finance.view` (D29). Rendered from ledger data (Q47) |
| POST | `/admin/payments/{id}/reverse` | SA | Reason required (Q31) |
| GET | `/admin/payment-submissions` | SA | Default filter `status=pending`; `limit` 1–500, default 200 |
| POST | `/admin/payment-submissions/{id}/verify`, `/reject` | SA | Verify creates the payment; reject needs a note |
| GET, POST | `/admin/refund-requests` | SA | GET: `limit` 1–500, default 200. POST: the amount can't exceed what is available after other requests that are waiting or approved |
| POST | `/admin/refund-requests/{id}/cancel` | SA | Withdraws a waiting request (409 `refund_state` otherwise) |
| POST | `/admin/refund-requests/{id}/approve`, `/reject` | SA | Since D75/D76 the one Administrator **approves their own request**: `self_approved: true`, audited as `refund.self_approved_sole_administrator`. They can't reject their own request (403 `second_person_required`: withdraw it instead). A request made by an account closed by D75/D76 is decided normally |
| POST | `/admin/refund-requests/{id}/mark-paid` | SA | Method and reference; posts the ledger entry |
| POST | `/admin/students/{id}/deposit/apply` | SA | Exit settlement: deposit → dues |

### 12.11 Leave

| Method | Path | Who | Notes |
|---|---|---|---|
| GET, POST | `/portal/me/leave-requests` | ST | 409 on overlap |
| POST | `/portal/me/leave-requests/{id}/cancel` | ST | Until the leave has ended |
| GET | `/admin/leave-requests` | SA | Default `status=pending` |
| POST | `/admin/leave-requests` | SA | Emergency leave on the student's behalf (Q35) |
| POST | `/admin/leave-requests/{id}/approve`, `/reject` | SA | Reject needs a note. The Administrator is notified of each request |

Approved leave is a record the Administrator and the student can both see.

### 12.12 Complaints

| Method | Path | Who | Notes |
|---|---|---|---|
| GET, POST | `/portal/me/complaints` | ST | Attachments via `file_id` |
| POST | `/portal/me/complaints/{id}/reopen` | ST | Within 7 days of resolution (Q40); needs a comment |
| GET | `/portal/me/complaint-categories` | ST | Active categories for the form (D50) |
| GET | `/admin/complaints` | SA | Filters: hostel, status, priority, assignee |
| GET | `/admin/complaints/{id}` | SA | With history, photos and the reopen deadline |
| POST | `/admin/complaints/{id}/assign`, `/start`, `/resolve`, `/comments` | SA | Resolve needs a note |
| GET, POST, PATCH | `/admin/complaint-categories` | SA (GET: any staff) | |

### 12.13 Mess

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/portal/me/mess-menu` | ST | `from`, `to` |
| GET | `/admin/hostels/{id}/mess-menu` | SA | Weekly view |
| POST | `/admin/hostels/{id}/mess-menu/entries` | SA (Q41) | Upsert a list of (date, meal, items) |
| POST | `/admin/hostels/{id}/mess-menu/copy` | SA | From another hostel or week (Q42) |

### 12.15 Announcements

| Method | Path | Who | Notes |
|---|---|---|---|
| GET, POST | `/admin/announcements` | SA | Hostel or organization-wide (SA) |
| GET | `/portal/me/announcements` | ST | |

### 12.16 Home overview

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/admin/overview` | SA | Active students, beds free and occupied, registration requests waiting, leave waiting, open complaints |

### 12.17 Audit logs

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/admin/audit-logs` | **SA only** (Q17) | Newest first, cursor paging. Filters: `q` (a name or Student ID: who did it, or the student, applicant or person it is about; 2–50 characters), `category` (`payments`, `refunds`, `students`, `registration`, `rooms`, `leave`, `complaints`, `notices`, `settings`, `signin`, `other`), `actor` (`system`, `students` or a user ID), `start`/`end` (the organization's dates), `action`, `entity_type`, `entity_id`, `actor_user_id`. Each item carries the stored row unchanged plus, for reading (D89): `sentence`, `category`, `actor_name`, `actor_kind`, `summary`, `details` (field, label, before, after; unchanged fields left out), `student_id`, `student_name`, `link_label`, `link_to` |

### 12.18 Student portal

`/portal/me/...`: `profile` (GET), `emergency-contact` (POST, Q46), `fees` (balance, ledger, receipts, UPI link), `payment-submissions` (POST with UTR and optional proof, Q28), `payments/{payment_id}/receipt.pdf` (own payments only), `leave-requests`, `complaints`, `mess-menu`, `announcements`. All are "me": no student ID ever appears in a portal URL.

### 12.19 Platform

| Method | Path | Notes |
|---|---|---|
| GET | `/platform/organizations` | Page-based; filters: status, `q` (name/slug) |
| GET | `/platform/organizations/{id}` | Profile + counts only |
| POST | `/platform/organizations/{id}/suspend`, `/reactivate` | Reason required (Q14) |
| GET | `/platform/health` | Jobs, email queue vs cap, last backup, last restore test, disk, error rate (Q50) |
| GET | `/platform/audit-logs` | Cursor; masked columns only (Q55). Viewing is itself audited. |
| GET, PATCH | `/platform/settings` | Health thresholds (D57); `PATCH {key, value, version}` |

## 13. How the frontend uses the API

- **Typed client:** TypeScript types generated from `/api/v1/openapi.json`; TanStack Query for fetching and caching.
- **Token handling:** on `401 token_expired`, one refresh runs (serialized across tabs with Web Locks), then the original request is retried once. If refresh fails, the user goes to the login page of the organization they were in (slug remembered in local storage, the only thing stored there).
- **Errors:** the UI shows `title`/`detail`, highlights `errors[].field` on forms, and always displays `request_id` on unexpected errors so problems can be traced in logs.
- **No CORS:** same origin. Development uses the Vite proxy.

## 14. Proposals in this document (for review)

1. Authenticated URLs carry no slug or org ID; the token decides the tenant (§2).
2. Separate signing key and audience for platform tokens (§4.4).
3. The password alone creates the session (D87).
4. Tokens travel in URL fragments (`#token=`), not query strings (§5.2, §5.7).
5. Refresh grace window of 10 s for two-tab races, plus Web Locks in the frontend (§5.6).
6. No automatic login after activation (§5.7).
7. HS256 access tokens with `kid` rotation (§5.6).
8. Rate-limit values (§5.10). *Accepted as Q72.*
9. Page-based pagination for bounded admin tables, cursor-based for feeds (§8).
10. Two-step uploads; unattached files purged after 24 h; 10 MB / 5 MB limits (§11.1).
11. 60-second signed links for standard files; no links at all for restricted files (§11.2).
13. The Platform Owner's password is reset through a command-line tool on the server (`python -m app.cli reset-platform-owner-password`).
14. API docs (`/api/v1/docs`) disabled when hosted (§3).

## 15. Questions from this document

Q67–Q70 were decided on 2026-11-07:
- **Q67:** session lifetimes as proposed.
- **Q68:** `snake_case`.
- **Q69:** admin re-activation for users without email (removed by D80: every student has an email).
- **Q70:** a `version` field with `409 edit_conflict`, instead of the proposed ETag/412.

No new questions.
