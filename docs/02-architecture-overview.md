# 02 — Architecture Overview

| | |
|---|---|
| Status | Updated 2026-11-07 to match all decisions Q1–Q63 in the project's decision log. Items still marked **PROPOSAL** were never asked as questions and are open for review. |
| Source of truth | the project brief ("§x" = brief section); decisions override the brief where marked. |
| Related | [01-understanding-and-requirements.md](01-understanding-and-requirements.md), [03-database-design.md](03-database-design.md), the project's decision log |
| Date | 2026-11-07 |

> **This document describes the current product** (12 November 2026): an organization has one role, **Administrator**, held by exactly one person (D75/D76), and its students. Everyone signs in with an email (or ID) and a password (D78, D80, D87). Email goes through Gmail when configured, Mailpit otherwise (D88). The hostel-scope machinery (§7) is kept in code, but every grant is organization-wide. the project's decision log is the decision record.

This is the *high-level* architecture. The full table design is in [03-database-design.md](03-database-design.md). API endpoints, the security analysis and the phase plan (brief Tasks 5–8) come later. SQL snippets here are illustrative; 03 is the precise version.

---

# Part A — Architecture

## 1. Design principles

1. **One of each.** One backend (a modular monolith), one database, one frontend app, one background worker (the same code as the backend, started differently). A new technology needs a concrete requirement behind it.
2. **The database enforces what must never break.** Application checks give friendly error messages; database constraints are the actual guarantee (no double bed booking, no edited payments, no cross-tenant rows). Application code has bugs and races; constraints don't.
3. **Defence in depth for tenant isolation.** Organization scoping in application code *and* PostgreSQL Row-Level Security, so a single bug in one layer doesn't leak data.
4. **Swappable edges.** Email sending, file storage and (later) payment gateways sit behind small interfaces so a provider can change without touching business logic.
5. **₹0 and local-only for now.** `docker compose up` on a Windows laptop runs everything. Deployment is out of scope, but nothing here blocks hosting later (Q1).
6. **Use a familiar stack.** The brief's stack covers almost everything. Additions are listed and justified below.

## 2. System at a glance

```
┌──────────────────────── Browser (laptop or phone) ────────────────────────┐
│  React + TypeScript single-page app — one app, three areas (Q51):         │
│  /admin (Administrator)   /portal (students)     /platform (Platform Owner)│
└──────────────────────────────────┬────────────────────────────────────────┘
                                   │ HTTPS · JSON · /api/v1/...
                                   ▼
┌──────────────── FastAPI API process (modular monolith) ───────────────────┐
│ core:    config · auth · tenancy · RBAC · audit · storage · email · jobs  │
│ modules: organizations · hostels · staff · students · allocations · fees  │
│          leave · registration · complaints · mess · overview              │
│          notifications · files · platform                                 │
└───────┬───────────────────────────────┬───────────────────────────────────┘
        │ SQL (one transaction/request) │ read/write files
        ▼                               ▼
┌─────────────────────┐        ┌─────────────────────┐
│ PostgreSQL 18       │        │ Upload storage      │
│ shared tables + RLS │        │ (Docker volume)     │
│ + jobs table        │        └──────────▲──────────┘
└───┬─────────────▲───┘                   │
    │ WAL archive │ claims jobs           │ reads attachments
    ▼             │                       │
┌────────────┐  ┌─┴───────────────────────┴──┐  SMTP  ┌──────────────────────────┐
│ pgBackRest │  │ Worker process (same code) ├───────►│ Mailpit, or Gmail (D88)  │
│ repository │  │ emails · scheduled jobs ·  │        │ real provider: later,    │
│(other disk)│  │ reminders · expiries       │        │ via the sender interface │
└────────────┘  └────────────────────────────┘        └──────────────────────────┘
```

| Component | Technology | Why |
|---|---|---|
| Frontend | React, TypeScript, Tailwind, Vite | Brief stack. Vite is the standard React build tool now that Create React App is deprecated. |
| API | FastAPI + Pydantic | Brief stack. Pydantic validates every input; FastAPI generates an OpenAPI schema the frontend uses (§3). |
| Data access | SQLAlchemy 2.0 (synchronous, Q52) + Alembic | Brief stack. Alembic gives versioned, reviewable schema migrations. |
| Database | PostgreSQL 18 (pinned) | Brief stack. 18 has had a year of fixes; 19 is very new. RLS, exclusion constraints, column privileges and `SKIP LOCKED` are all used. |
| Worker | Same Python codebase, different entrypoint | Sends email and runs scheduled jobs without blocking API requests. No Redis or Celery (Q54). |
| Backups | pgBackRest + `pg_dump` | Covers PITR, retention and verification (§12). |
| Email | Behind an `EmailSender` interface: Gmail SMTP when configured, Mailpit otherwise (D88) | Real email at ₹0 (about 500 a day); test addresses always go to Mailpit. |
| Packaging | Docker + Docker Compose | Brief stack. Same environment on Windows now and on a Linux server later. |

## 3. Frontend architecture

**One single-page app with three route areas** (`/admin`, `/portal`, `/platform`) sharing one design system (Q51). Each area is lazy-loaded, so a student's browser never downloads admin code. Nothing about security depends on which code the browser loads, because the backend enforces every permission.

**Libraries (all free and open source):**

| Need | Choice | Reason / trade-off |
|---|---|---|
| Routing | React Router | Standard, well documented. |
| Server data | TanStack Query | Caching, retries and background refresh without hand-written loading/error state. Also drives notification polling (§10). |
| Forms | React Hook Form + Zod | Fast forms with schema validation. Client validation is only for UX; the backend re-validates everything. |
| Components | shadcn/ui on Radix UI (Q53) | Tailwind-native, accessible, premium look. Components are copied into our repo, so we can restyle them, at the cost of more code to own. |
| Tables | TanStack Table | Headless sorting, filtering and pagination behind our own styled tables. |
| Icons | lucide-react | Matches shadcn/ui. |
| QR | Display only: `qrcode` (npm) draws the UPI payment code locally | No scanning anywhere. |
| API types | `openapi-typescript` generated from FastAPI's OpenAPI | A renamed backend field becomes a TypeScript compile error instead of a runtime bug. |

**Tokens in the browser:** the access token is kept **in memory only**. The refresh token is an **httpOnly, Secure, SameSite=Strict cookie** that JavaScript cannot read. On page reload the app calls the refresh endpoint to get a fresh access token. *Trade-off:* `localStorage` is simpler, but any XSS bug could read tokens stored there.

**Same origin:** in development, Vite proxies `/api` to FastAPI. When hosted later, one reverse proxy serves both from the same domain, so there's no CORS configuration and the SameSite cookie works naturally.

## 4. Backend architecture

### 4.1 Modular monolith

One deployable application split internally into modules with clear boundaries. It gives most of the organizational benefits of microservices without network calls between services, distributed transactions or multiple deployments.

```
backend/app/
  main.py
  core/          config, db (sessions, tenant context), security (JWT, hashing, one-time codes),
                 permissions, audit, storage, email (EmailSender), jobs, errors, logging
  modules/
    <module>/    router.py   HTTP layer: routes, dependencies, status codes
                 schemas.py  Pydantic request/response models
                 models.py   SQLAlchemy tables owned by this module
                 service.py  business rules + transactions (the module's public API)
  worker/        job runner + scheduler entrypoint
backend/migrations/   Alembic
backend/tests/
```

This layout is a PROPOSAL, settled when we scaffold.

- **Rule:** a module calls another module's `service.py`, never its tables directly. This is a convention at first; `import-linter` can enforce it later if it starts slipping.
- **No separate repository layer at first.** Services use SQLAlchemy sessions directly. A repository layer pays off mainly when swapping databases or mocking data access heavily. We test against real PostgreSQL instead (§15).

### 4.2 What happens on every request

1. **Request ID** is assigned and attached to all log lines and audit records.
2. **Authentication dependency** verifies the JWT, then loads the session row: is it still active, is the user active, is the organization not suspended (§6)?
3. **Database session** opens a transaction and sets the tenant context: `set_config('app.org_id', <org from token>, true)` (§8).
4. **Permission dependency**: does any of the user's role assignments grant e.g. `allocations.assign`? (§7)
5. **Pydantic** validates the request body and parameters, rejecting unknown or invalid fields.
6. **Service** applies business rules and checks hostel scope. It writes data, the **audit record** and any **jobs** in the *same transaction*.
7. **Commit** (or roll back everything on error). Errors come back in one consistent JSON shape. A resource in another organization returns **404, not 403**, because 403 would confirm it exists.

### 4.3 Synchronous SQLAlchemy (Q52)

**Synchronous SQLAlchemy 2.0 with psycopg 3**, endpoints written as plain `def`. FastAPI runs them in a thread pool. This gives a simpler mental model, no async lazy-loading errors and easier tests, and is plenty fast for hundreds of concurrent users. Async would matter only for very high concurrency of slow I/O, which we don't have.

### 4.4 Background worker and scheduler (Q54)

Work that must not block a request, or must happen on a schedule: sending email, fee charge runs, late fees, expiring reservations, announcement fan-out, auto-closing resolved complaints, purging expired security data and unattached uploads, and recovering stuck jobs.

- **Jobs table in PostgreSQL + a worker process.** The API inserts a job row **in the same transaction** as the business change (the *transactional outbox* pattern). If the transaction rolls back, the job never existed, so no "payment confirmed" email goes out for a payment that failed to save, and none is lost if the server crashes after commit.
- The worker claims jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, so two workers never grab the same job. It retries failures with exponential backoff and marks a job dead after N attempts. Dead jobs show on the platform health page.
- **Job payloads carry IDs only**, never personal data or secrets. The job loads what it needs under the right tenant context.
- The scheduler runs inside the worker and holds a PostgreSQL advisory lock, so only one scheduler is ever active.
- The worker connects as its own database role, `hms_worker` (§5). It may *claim* jobs of any organization, then sets that job's tenant context before doing the work, so RLS still protects everything the job touches.
- *Why not Celery + Redis?* Two extra technologies, and Redis can't join our database transaction, so we'd need an outbox anyway. *Why not FastAPI `BackgroundTasks`?* They live in memory: a restart loses them and there are no retries.

### 4.5 Additional backend libraries (all free)

| Library | Purpose | Note |
|---|---|---|
| PyJWT | JWT encode/verify | `python-jose` has had maintenance gaps. |
| argon2-cffi (directly or via `pwdlib`) | Argon2id password hashing | `passlib` is unmaintained. |
| Jinja2 | Email templates | Common in the FastAPI ecosystem. |
| Pillow | Re-encoding uploaded images (§9) | Strips metadata and hidden payloads. |
| cryptography | AES-256-GCM encryption of identity documents (Q65) | Approved. Keys live outside the database and project (03 §5.5.1). |
| ruff, mypy (dev only) | Lint/format and type checks | Local quality gate (§14). |


## 5. Database architecture

**Model (decided in §7 of the brief): one PostgreSQL database, shared tables, and an `org_id` column on every organization-owned row.**

| Approach | Isolation | Operations at 50+ orgs | Notes |
|---|---|---|---|
| Database per organization | Strongest | Many databases to migrate, back up and monitor | Too heavy for ₹0 and one developer. |
| Schema per organization | Strong | Every migration runs N times | Middle ground, still complex. |
| **Shared tables + `org_id` + RLS** | Good, *if* enforced in two layers | One migration, one backup, simple cross-organization counts for the Platform Owner | **Chosen.** The risk is one missing `WHERE org_id = ...`, which RLS and tests guard against (§8). |

**Key conventions** (full detail in 03):

- **UUIDv7 primary keys.** Not guessable, safe in URLs, and time-ordered so index inserts stay efficient.
- **`org_id NOT NULL`** on every tenant table, set by the server from the session.
- **Composite foreign keys including `org_id`** (and `hostel_id` down the hostel → building → floor → room → bed chain). The database refuses a row that links one organization's (or hostel's) record to another's, even if application code is wrong.
- **Uniqueness scoped to the organization**, e.g. `UNIQUE (org_id, student_code)`.
- **Money:** `numeric(12,2)` in the database and `Decimal` in Python, sent as strings in JSON. Never float.
- **History, not overwrites:** allocations have date ranges; the ledger is append-only; important statuses change through recorded transitions.
- **Status columns instead of deletes.** The app's database roles have no `DELETE` privilege on business tables.
- **Platform users are a separate table** from organization users. Platform Owner accounts have no `org_id`.

**Database roles:**

| Role | Used by | Privileges |
|---|---|---|
| `hms_owner` | Alembic migrations only | Owns the schema. Never used by the running app. |
| `hms_app` | API | Read/write on tenant tables, **subject to RLS**. INSERT-only on append-only tables (ledger, payments, audit, …). No DELETE. |
| `hms_worker` | Worker | Same as `hms_app`, plus claiming jobs of any organization and purging expired security rows (login attempts, old tokens and reset codes). |
| `hms_platform` | `platform` module only | **No `BYPASSRLS`.** It sees other organizations' data only through explicit read policies on a short list of tables, and only the **columns** granted to it (counts and metadata, masked audit log). It can suspend and reactivate organizations (§8). |
| `hms_backup` | pgBackRest / backup scripts | Backup privileges; records results in `backup_runs`. |

## 6. Authentication

**The organization comes from the email (D78, D80).** Administrator emails and student emails are each unique across the platform (`administrator_emails`, `student_emails`), so `POST /auth/login {email, password, portal}` asks a narrow database function (`administrator_org()` or `student_org()`, SECURITY DEFINER, EXECUTE only) for the organization, sets the tenant context, then checks the password. An unknown email and a wrong password get the same answer. The older `/o/{org-slug}/login` (Student ID or Employee ID, Q8) still works, and emailed links carry the slug (`/o/{org-slug}/activate`). Platform Owners use a separate `/platform/login` with no organization.

**Flow:**

```
email (or ID) + password ──► correct? ──► issue access + refresh tokens   (D87: no second step)
```

| Element | Design | Reason |
|---|---|---|
| Password hashing | Argon2id | Current recommendation; deliberately slow and memory-hard. |
| Access token | JWT, ~15 min (PROPOSAL), claims: user ID, org ID, session ID | Short lifetime limits damage if leaked. Roles are loaded per request, not trusted from the token, so role changes apply immediately. |
| Refresh token | Random secret, stored **hashed** in a `sessions` row, rotated on every use. The cookie value carries the org ID, session ID and secret, so the server can set the tenant context before checking it | Reuse detection: if an old refresh token is presented again, it was probably stolen, so the whole session family is revoked. |
| Session invalidation | Each request checks the session row (one indexed lookup) and the organization's status | Pure stateless JWTs can't be revoked early. One cheap query per request makes "log out everywhere", suspension and deactivation instant. |
| Second factor | None (D87): the password is the only factor | 05 §11 accepts this risk. Passwords need 8+ characters and must not be common; account lockout and IP limits stay. |
| Activation | Emailed link (Gmail or Mailpit, D88). Single-use, stored hashed, 7-day expiry; only one live link per user; the Administrator can send it again, which replaces the old link (D80) | Every student has an email (D80), so there is no copied-link path any more (Q2 changed). |
| Password reset | An emailed 6-digit code (D77): single use, 10 minutes, 5 wrong tries, stored hashed, limits per account and per IP; the same answer for unknown accounts | A database leak doesn't expose usable codes; brute force is capped. |
| Rate limiting | Database-backed failure counters per account and per IP, with increasing delays and temporary lockout | Works across processes without Redis. |
| Suspended organizations | Login refused; existing sessions stop working at the next request (Q14) | Org status is checked on every request. |

## 7. Authorization (RBAC + scopes)

Two checks on every action:

1. **Permission:** does the user's role allow it? Permissions are code constants such as `students.read`, `allocations.assign`, `payments.record`, `refunds.approve`, `leave.manage`. Each organization role is a fixed set of permissions defined in code (Q10).
2. **Scope:** is the target inside the user's reach?
   - **Organization scope:** guaranteed by tenancy (§8).
   - **Hostel scope:** role assignments are `(user, role, hostel_id or NULL)`, where NULL means organization-wide (Q11). Services filter every list and check every single record against the allowed hostels.
   - **Self scope:** students see only their own records. The student ID comes from the token, **never** from the URL (`/portal/me/fees`, not `/students/{id}/fees`).

**Decided rules (since D75/D76):**

| Rule | Decision |
|---|---|
| Roles | One organization role, `super_admin` (shown as **Administrator**), held by exactly one person; the database refuses a second active one (D76). No role assignment screens. |
| Administrator | Has **every permission in every hostel** of the organization (Q59). |
| Second-person approval | Refunds need approval by a *different* person than the requester (Q30), **except** while the organization has exactly one active Administrator, who may approve their own request; such approvals are marked self-approved (D76; trigger `refund_self_approval_guard`, constraint `refund_requests_second_person`). |
| Audit logs | Read-only. Administrator: own organization. Platform Owner: all organizations, personal data masked. Nobody else (Q17, Q55). |
| Identity documents | Administrator only (Q44). |

**Why hostel scope isn't in RLS:** hostel assignments change often and the rules are richer (it was designed for per-hostel roles; since D75 every grant is organization-wide). RLS stays a simple, hard **tenant** boundary; hostel scope lives in the app, where it's easier to test and change.

The frontend hides menu items a role can't use. That is **only for user experience**; security never depends on it.

## 8. Multi-tenancy

Four layers, so one failure doesn't leak data.

**Layer 1: tenant context comes only from the server.** The organization comes from the authenticated session, or from the URL slug before login. Any `org_id` in a request body is ignored or checked against the session.

**Layer 2: application scoping.** A SQLAlchemy session hook (`do_orm_execute` + `with_loader_criteria`) automatically adds `WHERE org_id = :current_org` to every ORM query on tenant models, and the server sets `org_id` on inserts.

**Layer 3: PostgreSQL Row-Level Security** on every tenant table:

```sql
ALTER TABLE students ENABLE ROW LEVEL SECURITY;
ALTER TABLE students FORCE ROW LEVEL SECURITY;      -- applies even to the table owner
CREATE POLICY tenant_isolation ON students
  USING      (org_id = app_current_org())
  WITH CHECK (org_id = app_current_org());
-- app_current_org() = NULLIF(current_setting('app.org_id', true), '')::uuid
```

At the start of each transaction the app runs `SELECT set_config('app.org_id', '<org uuid>', true)`. The final `true` makes it local to the transaction, so a pooled connection can't carry one organization's context into another request.

Details that commonly go wrong:

- **Fail closed:** with no context set, the policy matches **zero rows**.
- **The `NULLIF(..., '')`:** after a transaction ends, a custom setting already used on that connection reads as `''`, not NULL, and `''::uuid` would raise an error.
- **Owners and superusers bypass RLS.** So the app never connects as the owner, and we also use `FORCE`.
- **Unique constraints ignore RLS.** "Email already exists" could reveal another organization's data, so uniqueness includes `org_id`.
- **Materialized views can't carry RLS.** Any summary would use ordinary tables with `org_id` + RLS.
- **The worker sets context per job** (§4.4).

**Layer 4: automated proof:**
- A **cross-tenant test suite**: fixtures create Org A and Org B; for every endpoint, Org A's token tries Org B's IDs and must get 404 (reads) or be rejected (writes).
- A **schema test**: fail if any table with an `org_id` column lacks enabled + forced RLS and a policy, or if `hms_app` has any privilege on platform tables.
- An **SQL-level test**: as `hms_app` with no context set, every tenant table returns 0 rows.

**Platform Owner access (Q15, Q55), enforced by the database, not just the UI:** the `platform` module connects as `hms_platform`, which **does not bypass RLS**. Instead:

- A few tables have an extra policy `FOR SELECT TO hms_platform USING (true)`.
- On those tables, `hms_platform` is granted **only specific columns**. For example, on `students` it gets `id, org_id, hostel_id, status, created_at`, enough to count, but no names, contacts or documents.
- On `audit_logs` it gets everything **except** `before_values`, `after_values`, `ip_address` and `user_agent`. It therefore sees actions, actors' IDs, record IDs and the *names* of changed fields, with personal data masked (Q55). A query that asks for a masked column fails with a permission error.
- No impersonation (Q15). Unmasking isn't possible in v1; any future "break-glass" access would need a stated reason and an audit record.

The full list of what the Platform Owner can read is in 03 §6.

## 9. File storage

```
StorageBackend interface:  save(key, stream) · open(key) · delete(key) · exists(key)
  └── LocalFileStorage     (now)    → /data/uploads/{org_id}/{category}/{yyyy}/{mm}/{uuid}
  └── S3-compatible        (later, e.g. self-hosted MinIO) — only if needed
```

- **The database stores metadata:** category, storage key, original name, type, size, SHA-256, uploader, timestamps. Ownership comes from the record that references the file (student photo, document, payment proof, complaint attachment), so access checks follow that record's permissions.
- **Upload pipeline:** enforce the size limit while streaming → detect the real type from the file's first bytes → per-category allowlist (JPEG/PNG/WebP images, PDF) → **re-encode images** with Pillow (strips EXIF/GPS metadata and hidden payloads) → store under a random UUID name → temp file then atomic move → metadata row + audit entry in the same transaction.
- **The database double-checks** the type allowlist, the 10 MB maximum and the storage-key format (no `../`), so a bug can't record an unsafe path.
- **Downloads only through an authorized API endpoint**; the upload folder is never served as static files. Headers: `X-Content-Type-Options: nosniff`, `Content-Disposition: attachment` for documents, `Cache-Control: private, no-store`.
- **Files are write-once.** A replacement is a new file; the old one is soft-deleted and purged later. This keeps file backups consistent with database PITR (§12).
- **Identity documents** (Q44, Q62) are the most restricted files. Only the Administrator can open them; there are no shareable links and no caching, and every download is audited. Aadhaar uploads must be the masked Aadhaar PDF from UIDAI: the database requires a PDF, a masked number and the uploader's attestation (03 §5.6).
- **No virus scanning in v1** (Q45); revisit before any production use.
- **Generated receipts** are rendered as PDF from ledger data on demand (Q47), not stored as uploads. **Payment proofs** uploaded by students (Q28) are stored files.

## 10. Notifications

```
module event ("leave.approved") ─► notification service ─┬─► notifications table (in-app)
                                   (same DB transaction)  └─► jobs table (email) ─► worker ─► EmailSender
```

- **Modules only emit events.** The notification service decides who receives what on which channel, so modules stay independent.
- **In-app delivery: polling.** The app asks for the unread count every 30–60 s. WebSockets/SSE would add persistent connections and reconnect logic for freshness we don't need.
- **Email (Q1, Q2, D88):** an `EmailSender` interface over SMTP: Gmail (STARTTLS, app password) when `hms-mail.env` is filled in, Mailpit otherwise. Test addresses always go to Mailpit.
- **Priorities and a daily cap are built in now** but only matter once a real, limited provider exists:
  - **P0 security:** activation, password-reset code.
  - **P1 transactional:** payment confirmations, leave decisions, allocation.
  - **P2 bulk:** reminders, announcements. In-app first; email as a digest within the remaining quota.
- **Preferences (Q49):** announcement and reminder emails can be muted; security emails can't; in-app notifications can't be muted.
- Templates are Jinja2, with plain-text and HTML versions.

## 11. Home overview

The Administrator's home page runs one query of six counts (students, beds free and occupied from `bed_states`, registration requests, leave and complaints waiting), under RLS like every other query. The Platform Owner's organization list keeps its per-organization counts.

## 12. Backup and disaster recovery

**What PITR means:** a *base backup* is a full copy of the database files. *WAL* is PostgreSQL's ordered log of every change. With a base backup plus every WAL file since, we can restore the copy and replay changes up to any chosen moment.

**Tool: pgBackRest** (actively maintained; v2.60.0 released October 2026):

| §10 requirement | pgBackRest feature |
|---|---|
| Automated daily backups | Full / differential / incremental backups (run by a catch-up loop inside the database container, D58) |
| PITR | Continuous WAL archiving + `restore --type=time` |
| Rotation & retention | Retention settings, with expiry of old WAL |
| Integrity verification | Checksums on every file; `pgbackrest verify` |
| (bonus) | Compression and encryption of the repository |

*Alternatives considered:* `pg_dump` alone has no PITR; hand-written `pg_basebackup` scripts mean writing retention and verification ourselves; WAL-G is more cloud-oriented; Barman usually runs on a separate server.

**Plan:**

- WAL archived continuously, `archive_timeout = 60s`.
- Weekly full + daily differential backups; keep ~4 weeks (the PITR window). PROPOSAL values.
- **Also a daily `pg_dump -Fc`** (14-day retention): an independent copy made by a different tool.
- **Uploaded files:** daily incremental copy of the upload volume. Because files are write-once, restoring the database to an earlier time only leaves some unreferenced newer files, never missing ones.
- **Backup repository on a second internal drive or an external disk** (Q5), set with `HMS_BACKUP_DIR`. No cloud copy. **Until that is set, the default is a Docker volume on the same disk as the database** (D61): it protects against mistakes but not against disk failure, and the Platform health page shows a warning.
- **Verification:** `pgbackrest verify` daily, plus a **weekly automated restore test** into a throwaway directory (a temporary PostgreSQL on a private socket) with sanity queries. Results go into `backup_runs` and show on the Platform health page (Q50).
- **Monitoring the archiver:** if WAL archiving fails, WAL piles up until the disk fills and the database stops, so we watch `pg_stat_archiver`.
- **Data checksums on** (newer PostgreSQL versions enable them by default; confirm on the pinned image).
- **Runbook:** `docs/runbooks/restore.md`, written and rehearsed.
- **Docker:** a small custom image = official `postgres:18` + the `pgbackrest` package.
- **Targets (Q4):** RPO ≤ 5 min and RTO ≤ 4 h apply once hosted. On a Windows laptop that sleeps, "daily" means "as soon as the laptop is awake": at container start and every 15 minutes, any backup whose last successful run is older than its interval runs at once (D58).

## 13. How the components communicate

| From → To | How | Notes |
|---|---|---|
| Browser → API | HTTPS, JSON REST under `/api/v1/` | Access token in `Authorization: Bearer`; refresh via httpOnly cookie. |
| API → PostgreSQL | SQLAlchemy over TCP (psycopg) | One transaction per request; tenant context set first. |
| API → Worker | **Via the jobs table** | No message broker (§4.4). |
| Worker → PostgreSQL | Claims jobs (`SKIP LOCKED`), sets tenant context per job | Role `hms_worker`. |
| Worker → Email | `EmailSender` → SMTP → Gmail (STARTTLS, app password) or Mailpit (D88) | Test addresses always to Mailpit. |
| API/Worker → Files | Shared Docker volume via the storage interface | |
| PostgreSQL → Backup repo | pgBackRest `archive_command` (WAL) + scheduled backups | |

**Walkthrough 1: leave**

1. The student submits a leave request → saved as `pending` (or approved at once, for a local outing where the hostel doesn't require approval, Q35); the Administrator is notified.
2. The Administrator approves or rejects → in one transaction: status changes, audit record written, student notification + email job queued.
3. Approved leave is a record the Administrator and the student can both see.

**Walkthrough 2: recording a cash payment**

1. The Administrator submits the payment form with an **idempotency key** generated by the browser.
2. In one transaction: lock the student's row → take the next receipt number from the organization's counter → insert the payment → insert its ledger entries (e.g. dues −₹5,000, deposit +₹3,000) → allocate the dues credit to the oldest open charges (`ledger_allocations`) → audit record → queue receipt notification.
3. At commit, a database check confirms the ledger entries add up to the payment amount (03 §7).
4. A double-click or retry with the same idempotency key returns the first result instead of creating a second payment.

## 14. Local development on Windows (Docker Desktop + WSL2)

**Compose services:**

| Service | Image | Port (localhost only) | Purpose |
|---|---|---|---|
| `db` | postgres 18 + pgBackRest (custom) | 5432 | Database (data in a **named volume**) |
| `mailpit` | axllent/mailpit | 8025 (web UI), 1025 (SMTP) | Catches all outgoing email |
| `api` | backend image | 8000 | FastAPI with auto-reload |
| `worker` | backend image (other command) | — | Jobs + scheduler |
| `web` | node image | 5173 | Vite dev server (can also run natively on Windows) |

**Windows-specific points:**

- **Where the code lives (Q7):** on the Windows filesystem, with Docker Desktop on the WSL2 backend. File-watch **polling** is configured for uvicorn and Vite so auto-reload works through Windows bind mounts.
- **PostgreSQL data in a named Docker volume**, never a Windows bind mount (slow, and file-permission problems).
- **Backups bind-mounted to a different drive or external disk** (Q5). Named volumes live inside the same WSL2 virtual disk as the database.
- **Line endings:** a `.gitattributes` forcing LF. Scripts saved with CRLF fail inside Linux containers (`/bin/sh^M: bad interpreter`).
- **Memory:** cap WSL2 with `.wslconfig` (4–6 GB, depending on the laptop's RAM).
- **Secrets:** `.env` (never shared) with a shared `.env.example`. Never log secrets; keys live outside the project folder.
- **Packages and Git (Q6):** installing from npm, PyPI and Docker Hub is fine. The code is on GitHub (D90), but there is no hosted CI. Instead, one local script runs ruff, mypy, pytest, eslint, `tsc` and vitest inside the containers, plus `pip-audit`, `npm audit` and gitleaks (Q73).

## 15. Testing approach

- **Backend:** pytest + FastAPI TestClient against a **real PostgreSQL** in Docker. SQLite can't run RLS policies, exclusion constraints, column privileges or `SKIP LOCKED`, so tests on it would pass while the real system fails. Each test runs in a rolled-back transaction or a database cloned from a template.
- **Test suites that are non-negotiable here:**
  - cross-tenant isolation and the RLS schema test (§8)
  - the Platform Owner's column limits (selecting a masked column must fail)
  - role × endpoint permission matrix, including the Administrator's blanket access and the different-approver rule
  - concurrency: double allocation, double payment, the one-Administrator rule
  - ledger invariants (03 §8)
  - secret leakage (run auth flows with known secret values, then assert they appear in no log line or audit row)
  - migrations (empty database → latest)
- **Frontend:** Vitest + React Testing Library. Later, a few end-to-end flows with Playwright (free, Apache-2.0).

## 16. Zero-budget check

| Item | Cost status | Notes |
|---|---|---|
| React, TypeScript, Tailwind, Vite, shadcn/ui, Radix, TanStack, lucide | **Free** (MIT / Apache-2.0) | — |
| Python, FastAPI, SQLAlchemy, Alembic, Pydantic, pytest, PyJWT, argon2-cffi, pyotp, Jinja2, Pillow | **Free** (open source) | — |
| PostgreSQL, pgBackRest, Mailpit | **Free** (open source) | Mailpit never delivers real email. |
| Docker Engine + Compose | **Free** (open source) | — |
| Docker Desktop | **Free with conditions** | Free for personal use, education, non-commercial open source and small businesses (<250 employees *and* <US$10M revenue). |
| npm / PyPI / Docker Hub | **Free**, allowed (Q6) | Docker Hub limits anonymous image pulls; fine for us. |
| Domain name | **Not purchased** (Q2) | Needed only for hosting and real email later. |
| Production hosting | **Out of scope** (Q1) | When it comes back: free tiers usually sleep, wipe disks and lack PITR; "always free" VMs usually require card verification. |
| Real email delivery | **Not configured** (Q2) | Free tiers allow roughly 100–500 emails/day and mostly need a domain (B7). |
| Payment gateway | **Not used** (Q28) | Per-transaction fees + business KYC. v1 uses UPI links + UTR verification. |
| SMS / WhatsApp | **Costs money per message** | Deferred indefinitely. |
| CAPTCHA | Not used (Q13) | — |
| Off-site/cloud backup | Not used (Q5) | External drive instead. |
| ClamAV | **Free**, not used in v1 (Q45) | Needs ~1 GB+ RAM. |
| Fonts (e.g. Inter) | **Free** (SIL Open Font License) | Self-hosted. |

---

# Part B — Major technical challenges

Each covers: **what can go wrong → approach → how we prove it works.** Exact constraints and triggers are in [03-database-design.md](03-database-design.md).

## B1. Tenant isolation

**What can go wrong**
- A query without `org_id` returns another organization's students (the classic multi-tenant bug).
- An endpoint trusts `org_id` or `hostel_id` from the request.
- A child row points at another organization's parent.
- Error messages or unique-constraint failures reveal that data exists elsewhere.
- Background jobs run without tenant context.
- Platform tools become a backdoor.

**Approach:** the four layers in §8: server-side context → automatic ORM scoping → RLS with `FORCE` and non-owner roles → automated tests. Composite foreign keys with `org_id`, 404 instead of 403, organization-scoped unique constraints, per-job context in the worker. The Platform Owner's role has **no RLS bypass**, only explicit policies and column grants, so platform tools can't become a backdoor.

**Proof:** cross-tenant suite against every endpoint; schema test fails the build if any tenant table lacks RLS; SQL-level zero-rows test; a test that `hms_platform` gets a permission error on masked columns.

## B2. Bed allocation concurrency

**What can go wrong:** two managers assign the same vacant bed at the same moment. Both screens checked "is it free?", both saw "yes", both saved. This is a *check-then-act race*: no application `if` prevents it, because the check and the save are separate steps.

**Approach:** let the database decide. Allocations (including reservations, Q21) are rows with a date range, and **exclusion constraints** forbid overlapping ranges for the same bed, and for the same student:

```sql
EXCLUDE USING gist (bed_id WITH =, stay WITH &&)     WHERE (status IN ('reserved','active','ended'))
EXCLUDE USING gist (student_id WITH =, stay WITH &&) WHERE (status IN ('reserved','active','ended'))
```

- This covers today's occupancy *and* future reservations. A bed reserved from 1 July can't be given to someone else whose stay overlaps.
- The service first locks **the student's row, then the bed's row** (`SELECT ... FOR UPDATE`, always in that order) before checking maintenance status and the hostel's resident type (Q19). The losing request then waits, and hits the constraint against a *committed* row: a clean "bed was just taken" (HTTP 409). Without these locks the database still lets only one win, but two simultaneous conflicting inserts can end with the loser aborted as a deadlock (`40P01`) instead of a constraint error. The Phase 0 proof suite showed this on the real server (03 §13).
- **Occupancy isn't stored as a flag** that could drift. It's derived from allocations; only manual states (maintenance, inactive) are stored on the bed.
- **A transfer** ends the current allocation and creates the new one in **one transaction**. The hostel is carried by the bed, so **inter-hostel transfers later need no schema change** (ALC-6).

**Proof:** a test starts two database sessions that allocate the same bed at the same moment (synchronized with a barrier); exactly one succeeds. Same for one student and two beds, and for reservations overlapping occupancy.

## B3. Payment ledger integrity

**What can go wrong:** an edited or deleted payment erases evidence; a double-click records a payment twice; the same UPI transaction is entered twice; floating-point rounding; receipt numbers with gaps or duplicates; late fees applied twice when a job reruns; a refund issued by one person alone; a deposit refunded twice.

**Approach**
- **Append-only ledger with two balances per student** (detail in 03 §5.8):
  - **Dues**, which the student owes: charges, late fees and fines add to it; payments, waivers and deposit applications reduce it.
  - **Deposit held**, which the organization holds for the student: deposits received add to it; deposit refunds and applications reduce it. Deposits are never counted as revenue.
- **The database enforces the sign and account for every entry type** with one CHECK constraint. Ledger and payment rows **can't be updated or deleted**: the app roles lack the privilege, and a trigger blocks it anyway.
- **Mistakes are fixed with reversal entries**: an exact opposite amount, at most once per entry, with a reason. A trigger checks the reversal matches its original. Reversing a payment requires reversing all of its entries (Q31).
- **Partial payments (Q60):** every link between a credit (payment, waiver, deposit application) and the charge it settles is stored in `ledger_allocations`. By default the app applies credits to the oldest open charges first; the table lets the Administrator target a specific charge later without changing the ledger. The database keeps every allocation within both amounts.
- **Idempotency:** `UNIQUE (org_id, idempotency_key)` on payments; `UNIQUE (org_id, method, reference)` for UPI/bank references; one pending/verified submission per UTR (Q28).
- **Receipt numbers (Q29):** a per-organization, per-financial-year counter row incremented inside the payment transaction. If the payment fails, the increment rolls back too, so there are no gaps. *Why not a PostgreSQL sequence?* Sequences don't roll back. The printed number (`RCPT/2026-27/000123`) is a **generated column**, so it can never disagree with the sequence number.
- **Cross-row rules checked by the database at commit:** a payment's entries add up to the payment amount; a deposit application moves the same amount out of "deposit" as into "dues".
- **Balance floors checked under a per-student lock:** deposit held never goes below zero; a dues refund never exceeds the student's credit.
- **Late fees (Q26):** one entry per overdue charge per day (or once, for flat fees), with a unique key, so rerunning the job can't double-charge. The cap is applied by the job.
- **Refunds (Q30, Q59):** a different person approves (`CHECK (decided_by <> requested_by)`), except the organization's sole Administrator, whose own approval is allowed and marked self-approved (D76). A refund entry can only be posted against an *approved* request, exactly once.
- **Gateway-independent:** a future gateway would only be another way of creating payments.

**Proof:** property-style tests that generate random sequences of charges, payments, reversals, refunds and deposit applications, then check the invariants: balances = Σ entries, reversals net to zero, receipt numbers gap-free, deposits never negative, no deposit counted as revenue. Concurrency tests for duplicate submissions.

## B5. Audit logging

**What can go wrong:** an audited change saved without its audit row (or the reverse); audit rows that can be edited or deleted; secrets leaking into the log; records too vague to answer "who changed this and from what?"; personal data exposed to people who shouldn't see it.

**Approach**
- **Semantic events written by the service layer** (e.g. `allocation.transferred` with `{from_bed, to_bed}`) in the **same transaction** as the change.
- **Recorded per §9:** actor (user or platform user, with roles at the time), organization, hostel, action, entity type + ID, timestamp, names of changed fields, before/after values, request ID, IP, user agent.
- **Before/after values use an allowlist** of auditable fields per model; password hashes, tokens and one-time codes are never on it. Request metadata is an allowlist too.
- **Immutability:** the app roles have INSERT and SELECT only, plus a trigger that rejects updates and deletes. Optional later: a hash chain so even a database administrator's tampering is detectable.
- **Who reads it (Q17, Q55):** the Administrator, own organization, full detail. Platform Owner, all organizations, **with before/after values, IP and user agent masked by column privileges** (§8). Students never.
- **Shown in plain language (D89):** the Administrator's audit page turns each row into a sentence ("Kavita Deshpande approved a refund of ₹2,500 for Saurabh Parikh"), grouped by day, with a category, a short summary and the before and after on demand. The amount and the names come from the record the row points to, at read time; the stored row is never changed.
- **Application logs ≠ audit logs.** Logs are structured JSON to stdout with a redaction filter and no request bodies on auth routes.

**Proof:** each audited action creates exactly one correct audit row; UPDATE/DELETE on the audit table fails for app roles; the Platform Owner can't select masked columns; the secret-leakage test (§15).

## B6. PITR backups

**What can go wrong:** backups that can't actually be restored (the most common failure: nobody tested); WAL archiving silently failing until the disk fills; backups on the same disk as the data; database restored without matching files; a restore procedure nobody remembers.

**Approach:** §12 in full: pgBackRest with continuous WAL archiving, retention, `verify`, an independent `pg_dump`, the backup repository on a separate disk (Q5), write-once files, archiver monitoring, a **weekly automated restore test** recorded in `backup_runs` and shown on the Platform health page, and a rehearsed runbook.

**Honest limitation:** while everything runs on a laptop that sleeps (Q1), backups only happen when the machine is on, and there is no off-site copy. That's acceptable for local development, but it must be revisited before hosting.

**Proof:** the restore test, plus a documented drill: "restore to 10 minutes before a deliberately bad UPDATE and show the data is back".

## B7. Email delivery

Email is needed for organization verification, student activation, student registration, password reset and notifications.

**Decision (Q1, Q2; D88):** Gmail SMTP with an app password when `hms-mail.env` is filled in (no domain needed, ₹0, about 500 a day), Mailpit otherwise; test addresses always go to Mailpit. The design:
1. **`EmailSender` interface:** switching to a real provider later is configuration plus, at most, one new class.
2. **Everything that must arrive is retried:** activation and password-reset emails go first in the priority queue; the Administrator can send the activation email again (D80).
3. **Sign-in never depends on email:** the password is enough (D87); only a forgotten password needs the emailed code.
4. **Priority queue and daily cap already in place**, ready for a limited provider.

**For later (reference only; verify current limits when the time comes):**

| Option | Free limit (as of 2026-11) | Main catch |
|---|---|---|
| Gmail SMTP (personal account + app password) | ~500 recipients per rolling 24 h | `@gmail.com` sender; requires 2-Step Verification; may be throttled. No domain needed. |
| Brevo free plan | 300 emails/day | Brevo branding; realistically needs your own domain. |
| Resend free plan | 100/day, 3,000/month | Requires a domain you own. |
| SendGrid | No permanent free plan since 2025 | Not an option. |

## B8. Other challenges worth knowing about now

- **Scope in list endpoints.** Tenant isolation is the hard boundary (§8). The hostel-scope checks (§7) still run on every list and record, so per-hostel roles could come back without new leaks, but since D75 every grant is organization-wide.
- **Time zones and "day" boundaries.** Leave dates, due dates and late-fee dates follow the hostel's local midnight. Store UTC and compute "local today" explicitly.
- **Upload attacks.** Disguised files, oversized uploads, path tricks: handled by §9's pipeline plus database checks.
- **Counts vs RLS vs performance.** Indexes must start with `org_id`; no materialized views.
- **Self-registration abuse.** Rate limits + email verification + Platform Owner suspension (Q13).
- **Windows development friction.** File-watching, line endings and WSL2 memory (§14).

---

## Sources (free-tier figures and tool status, checked 2026-11-07)

- Brevo free plan (300 emails/day): [costbench.com](https://costbench.com/software/marketing-automation/brevo/free-plan)
- Resend free plan (100/day, 3,000/month, 1 domain): [resend.com/pricing](https://www.resend.com/pricing.md), [resend.com blog — new free tier](https://resend.com/blog/new-free-tier)
- SendGrid free plan retired 2025: [Twilio changelog](https://www.twilio.com/en-us/changelog/changes-coming-to-sendgrid-s-free-plans), [SendGrid trial plan](https://support.sendgrid.com/hc/en-us/articles/35270136965403)
- Gmail SMTP limits (~500 recipients/24 h, app password requires 2-Step Verification): [Nodemailer — Using Gmail](https://nodemailer.com/usage/using-gmail), [Nylas — Gmail SMTP settings](https://developer.nylas.com/docs/cookbook/email/gmail-smtp-settings/)
- pgBackRest 2.60.0 (October 2026): [postgresql.org announcement](https://www.postgresql.org/about/news/pgbackrest-2600-released-3416/)
