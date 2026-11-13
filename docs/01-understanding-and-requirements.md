# 01 — Understanding and Requirements

| | |
|---|---|
| Status | Decisions from the project's decision log applied on 2026-11-07. |
| Source of truth | the project brief. "§x" refers to a section of the brief. Where a decision changes the brief, the decision wins and the change is marked. |
| Related | [02-architecture-overview.md](02-architecture-overview.md), the project's decision log |
| Date | 2026-11-07 |

> **This document describes the current product** (12 November 2026): an organization has one Administrator, the person who registered it, and its students; everyone signs in with an email (or ID) and a password; the Administrator's home shows a few counts; email goes through Gmail when configured, Mailpit otherwise. How it got here is in the project's decision log, the decision record.

**Labels used in this document**

- **DECIDED (Qn)**: settled; the full wording is in the project's decision log.
- **PROPOSAL**: a suggested default for something never raised as a question. Not yet a decision.
- **→ Qn**: an open question that still needs an answer.
- Anything without a label is taken directly from the brief.

---

## Part 1 — The product in short

### 1.1 What it is

This is a web platform that many independent organizations share. Examples are a university, a college or a private hostel company. Each organization uses it to run its hostels: the physical space (which bed is free), the residents (who lives where), the money (what each resident owes and has paid), and daily operations (leave, complaints, the mess menu, announcements).

We run one copy of the software and one database, but each organization's data must be completely invisible to every other organization. In SaaS terms, each organization is a **tenant**. The **Platform Owner** (us) operates the platform itself and sits above all tenants.

For now the platform runs **locally with Docker Compose only**. Deployment is out of scope, but nothing may be designed in a way that blocks hosting later (DECIDED Q1).

### 1.2 The problems it solves

| Without a system (typical today) | What the platform provides |
|---|---|
| Bed occupancy is kept in registers or spreadsheets, so two students can be given the same bed and nobody has a reliable count of vacant beds. | A single live view of every bed (vacant, occupied, reserved, under maintenance), with the database preventing double allocation. |
| Fees are tracked by hand. Partial payments, deposits, late fees and refunds are easy to get wrong, receipts are inconsistent, and disputes cannot be settled from the records. | A configurable fee system with a transaction history that cannot be silently edited, consistent receipts, and outstanding balances computed from that history. |
| Leave approval is slow and on paper, and nobody knows which students are away. | Leave request → Administrator approval in the app, with a list of who is on approved leave. |
| Hostel rules, menus and notices are passed around on paper. | Announcements, a weekly mess menu and in-app notifications in the Student Portal. |
| Complaints get lost, and nobody is clearly responsible for them. | Categorized, prioritized, assigned complaints with status tracking and history. |
| Nobody can see who changed what. | A mandatory audit trail for important actions. |
| Nobody knows at a glance what needs attention. | A home page with the counts that matter: students, free and occupied beds, and what is waiting (registration requests, leave, open complaints) (D86). |
| Each institution would otherwise need its own installation. | One shared platform, with organizations signing themselves up. |

### 1.3 Who uses it

| Role | One-line description | Data scope |
|---|---|---|
| Platform Owner | Operates the SaaS: oversees organizations, platform health and global settings. | **Platform**: organization metadata and aggregate counts, plus audit logs of all organizations showing actions and record IDs, with personal data masked (DECIDED Q15, Q17, Q55). |
| Administrator | The one person who runs an organization: hostels, students, allocation, fees, leave, complaints, mess menu, announcements and the audit log (D75, D76). | **Organization**: every hostel in their organization. |
| Student / Resident | Uses the Student Portal for their fees, leave, complaints, the mess menu, announcements and their profile. | **Self**: own records only. |

**Not users:** parents and guardians. Their contact details are stored on the student record for emergencies only (§4). There is no parent portal or parent login, and no automatic guardian messages in v1 (DECIDED Q36).

**One role (D75, D76):** each organization has exactly one Administrator account, the person who registered it; the database refuses a second one.

### 1.4 How the roles relate

```
Platform Owner ........................... platform scope (outside every organization)
  │  oversees: suspend/reactivate, global config, all audit logs
  ▼
Organization (tenant) .................... data fully isolated from other organizations
  │
  ├─ Administrator ....................... organization scope (exactly one, D76)
  │
  └─ Student / Resident .................. self scope
        ▲ admitted by the Administrator, or registered by the student and approved (D74)
        Guardian = contact details on the student record, NOT a user
```

1. **Who manages whom.** The Platform Owner does *not* create organizations. Organizations register themselves (§6.1); the person who registers becomes the Administrator. The Administrator admits students or approves their registration requests (D74).
2. **Workflows:**

| Workflow | Handoff chain |
|---|---|
| Admission | Administrator admits (email required, D80) → the system emails an activation link → the student sets a password. Or: the student registers, verifies the email, and the Administrator approves (D74) |
| Allocation | Administrator assigns a bed → fees may depend on room type (DECIDED Q20). Students don't see their room (D79) |
| Leave | Student requests → Administrator approves or rejects → the leave is recorded |
| Fees | Administrator configures fees → charges reach the student → payment recorded (cash, or UPI verified from the student's UTR) → receipt issued |
| Complaints | Student submits → Administrator triages and resolves → student sees status, can reopen within 7 days (DECIDED Q40) |

**Interfaces (§5):** one **Admin Dashboard** for the Administrator and a separate **Student Portal**. Both must feel like the same product. DECIDED (Q51): one React app with three lazy-loaded areas: `/admin`, `/portal` and `/platform` (Platform Owner, no organization context).

---

## Part 2 — Requirements

### 2.1 Functional requirements by module

IDs (e.g. `ALC-5`) are for tracing a requirement through design, code and tests.

#### Organization & platform (§2, §4A, §6.1, §14)
- **ORG-1** Self-registration: submit organization details → create the Administrator account → email verification → workspace created → the Administrator sets up hostels. Active immediately after email verification; registration is rate-limited; no CAPTCHA in v1 (DECIDED Q13).
- **ORG-2** Organization profile, configuration and organization-specific settings, including the organization's timezone (DECIDED Q48).
- **ORG-3** Platform Owner manages organizations: view, suspend, reactivate. Suspension logs out and blocks all users, stops jobs and emails, and keeps data unchanged (DECIDED Q14).
- **ORG-4** Multiple hostels per organization, with no fixed limit.
- **ORG-5** Platform Owner: organization list with counts, global configuration, platform-level security, and a platform health page (contents DECIDED Q50). The Platform Owner sees organization metadata and counts, not student personal data, and has no impersonation (DECIDED Q15). In audit logs the Platform Owner sees actions and record IDs, with personal data masked (DECIDED Q17, Q55).
- **ORG-6** Subscription billing is deferred, but the architecture must allow it to be added later (§14).
- **ORG-7** Each organization has a unique, URL-safe slug. Sign-in needs only an email and a password; the organization is found from the email (D78 for the Administrator, D80 for students). `/o/{org-slug}/login` still works (DECIDED Q8, D78).
- **ORG-8** Organization-defined academic sessions (e.g. 2026–27) with start and end dates; one is current. Allocations, fees and reports are tied to a session (DECIDED Q18).

#### Hostel structure (§6.2)
- **HST-1** Hierarchy Organization → Hostel → Building → Floor → Room → Bed, with management at each level.
- **HST-2** Hostel capacity. PROPOSAL: derived from the number of active beds, not typed in manually.
- **HST-3** Hostel-specific settings: the leave rules (whether local outings need approval, Q35).
- **HST-4** Optional hostel resident type: boys / girls / co-ed (DECIDED Q19).
- **HST-5** Room types per hostel (e.g. double, AC) that fee structures can target (DECIDED Q20).
- **HST-6** CSV import of rooms and beds in v1 (DECIDED Q23).

#### The Administrator (§4, §6.11; D75, D76)
- **STF-1** One Administrator per organization, created at registration, with an Employee ID and an email unique across the platform (D78).
- **STF-2** No other staff accounts and no role assignment: the Administrator holds every permission in every hostel.
- **STF-3** A forgotten password is reset with an emailed 6-digit code (D77).

#### Student admission (§6.3)
- **STU-1** Manual admission by the Administrator: choose hostel, enter student details, guardian contact (emergency only), upload documents.
- **STU-2** System generates a Student ID that is unique within the organization.
- **STU-3** The account starts inactive. An activation link is emailed, the student sets a password, and the account becomes active. Passwords are never sent by email. Every student has an email, unique across the platform (D80); the Administrator can send the activation email again, which replaces the old link. (The copied-link fallback of Q2 was removed by D80.)
- **STU-4** Student record: personal, contact and emergency contact information, gender (DECIDED Q19), admission details, documents, account status, room allocation history. **Student photo is mandatory** (DECIDED Q39).
- **STU-5** Student Portal: own profile, fees and payment history, leave requests, complaints, mess menu, announcements and notifications.
- **STU-6** Exit: check-out + final settlement (dues, deposit refund). The portal stays read-only for 90 days, then the account is deactivated; records are kept per retention policy (DECIDED Q22).
- **STU-7** Students can update their own phone number and emergency contact. Identity fields are admin-only. All changes are audited (DECIDED Q46).

#### Room & bed allocation (§6.4)
- **ALC-1** Manual allocation of a student to a bed by the Administrator. Blocked if the student's gender doesn't match the hostel's resident type (DECIDED Q19).
- **ALC-2** Bed states: vacant, occupied, reserved, under maintenance. A reservation is for a named student with an expiry date and is released automatically when it expires (DECIDED Q21).
- **ALC-3** Check-in and check-out dates, and full allocation history per student and per bed, tied to an academic session (Q18).
- **ALC-4** Room transfers (within a hostel), keeping history.
- **ALC-5** Duplicate bed assignment must be impossible. A student should also hold at most one active bed. This is implied, not stated.
- **ALC-6** Transfers between hostels are not decided yet, but the data model must allow them later without major changes.

#### Fees & payments (§6.5)
- **FEE-1** Configurable fee structures: billing cycles one-time, monthly, per term/semester, annual (DECIDED Q24); hostel-specific and room-type-specific fees (Q20); student-specific charges. No automatic proration in v1; mid-cycle differences are manual adjustments (DECIDED Q25).
- **FEE-2** Record cash, UPI and online payments, including partial payments. "Online" in v1 = no gateway. The portal shows the organization's UPI ID and a QR/link with the amount pre-filled; the student pays directly and submits the UTR plus an optional screenshot; the Administrator verifies it and records the payment (DECIDED Q28).
- **FEE-3** Outstanding balances; security deposits; late fees (grace days plus a flat amount or a capped per-day amount, applied by a safely rerunnable daily job, DECIDED Q26); discounts, waivers and fines as adjustment entries with a mandatory reason (DECIDED Q27); refunds with maker-checker approval, nobody approving their own request (DECIDED Q30).
- **FEE-4** Receipts with gap-free numbers per organization per financial year, e.g. `RCPT/2026-27/000123`; no GST in v1 (DECIDED Q29). Transaction history; financial reports (CSV export; receipts as PDF, DECIDED Q47).
- **FEE-5** Accounting logic must be independent of any payment gateway. A gateway must not be assumed to be free.
- **FEE-6** Recorded payments are never edited or deleted, only reversed with a reason (DECIDED Q31).

#### Leave (§6.7, changed by D75)
- **LEV-1** A student submits a leave request; the Administrator approves or rejects it. Leave types: home leave (multi-day, approval required), local outing (same day, approval configurable per hostel), emergency (the Administrator can create it for the student) (DECIDED Q35).
- **LEV-2** Approved leave is a record the Administrator and the student can see.
- **LEV-3** The student can cancel a request until it has ended; every decision is audited.

#### Complaints & maintenance (§6.8)
- **CMP-1** Students submit complaints with a category and priority (attachments allowed, §11). No anonymous complaints (DECIDED Q40).
- **CMP-2** Assignment to staff, status tracking, resolution records. Visible only to the submitter and staff; priority sets a target date; no automatic escalation in v1; students can reopen within 7 days of resolution (DECIDED Q40).
- **CMP-3** Complaint history: every status change, comment and attachment is kept.

#### Mess (§6.9)
- **MES-1** Daily menu (breakfast, lunch, evening snacks, dinner), weekly calendar, visible in the Student Portal. No mess billing or consumption tracking in the first version.
- **MES-2** Managed by the Administrator (D75). Per hostel, with "copy from another hostel/week"; no dietary tags in v1 (DECIDED Q42).

#### Notifications (§6.12)
- **NTF-1** In-app and email channels. **Email:** Mailpit for all development; email sits behind a sender interface so a real provider can be plugged in later; no domain purchase (DECIDED Q2).
- **NTF-2** Events: fee reminders, payment confirmations, leave approvals and rejections, complaint updates, hostel announcements, admission updates. In the app, the bell shows the true unread count ("99+" above 99) and opens the list; clicking a notification marks it read and opens its page; "Mark all as read" clears it. Every notification opens a page that exists, or none (D84).
- **NTF-3** Hostel announcements, written by the Administrator.
- **NTF-4** SMS and WhatsApp only if they can be done at ₹0. In practice they are deferred.
- **NTF-5** Non-critical emails (announcements, reminders) can be muted; security emails can't; in-app notifications can't be muted (DECIDED Q49).

#### Home overview (§6.13)
- **ANL-1** The Administrator's home page shows: active students, beds free and occupied, registration requests waiting, leave waiting, open complaints.
- **ANL-2** The Platform Owner sees each organization's basic details and counts (hostels, beds, students).

#### Authentication & security (§8)
- **AUTH-1** Students and the Administrator sign in with **email and password**; the organization is found from the email, which is unique across the platform for students (D80) and for Administrators (D78). Unknown email and wrong password get the same answer. The older `/o/{org-slug}/login` page (Student ID or Employee ID, Q8) still works.
- **AUTH-2** Emailed activation link (STU-3) and password reset by an emailed 6-digit code (D77).
- **AUTH-3** Everyone signs in with the email (or ID) and the password (D87). Passwords need at least 8 characters and must not be common. A forgotten password is reset with an emailed 6-digit code; the Platform Owner's on the server.
- **AUTH-4** JWT access tokens, refresh tokens, session invalidation, login rate limiting.
- **AUTH-5** RBAC enforced on the backend, not just by hiding UI.

#### Audit logging (§9)
- **AUD-1** Record who acted, organization, hostel (where applicable), action type, affected record, timestamp, before and after values where appropriate, and request metadata.
- **AUD-2** Ordinary users cannot modify or delete audit records.
- **AUD-3** Never record passwords, tokens, OTPs or other secrets.
- **AUD-4** Viewing is read-only for the Administrator (own organization) and the Platform Owner (all organizations). No other role can view audit logs. Keep everything in v1; define a retention period before any production use (DECIDED Q17). The Platform Owner sees actions and record IDs, with personal data masked (DECIDED Q55).
- **AUD-5** The Administrator reads the log as plain sentences grouped by day, filtered by name, category, date and who did it, with the before and after values on demand (D89). Presentation only.

#### Files (§11)
- **FIL-1** Files are stored on the local filesystem with metadata and ownership in PostgreSQL, behind a storage abstraction so the provider can be swapped later.
- **FIL-2** Organization and user permissions enforced; type and size validated; safe file names; no unauthorized access. Identity documents are the most restricted files: only the Administrator can open them, and every download is audited. Aadhaar uploads must be the masked Aadhaar PDF from UIDAI (DECIDED Q44, Q62).
- **FIL-3** File kinds: student photos (mandatory, Q39), identity documents, admission documents, payment proofs uploaded by students (Q28), complaint attachments. System-generated receipts are rendered from ledger data, not stored as uploads.
- **FIL-4** No virus scanning in v1. Strict file-type allowlist plus image re-encoding; revisit before production (DECIDED Q45).

#### Backup & recovery (§10)
- **BAK-1** Automated daily backups, PostgreSQL Point-in-Time Recovery (PITR), rotation and retention, integrity verification, periodic restore tests, recovery documentation. Free tools and local storage only.
- **BAK-2** The backup repository lives on a second internal drive or external disk; no cloud storage (DECIDED Q5). RPO ≤ 5 min and RTO ≤ 4 h are targets for a future hosted environment; locally, backups are best-effort (DECIDED Q4).

### 2.2 Who can do what (derived from §4, updated with decisions)

`M` = manage, `V` = view, `own` = own records only, `—` = no access

| Module | Platform Owner | Administrator | Student |
|---|---|---|---|
| Organizations | M (suspend/reactivate) | M (own org) | — |
| Hostel structure | aggregates only (Q15) | M | — |
| Students | aggregates only (Q15) | M | own (limited edits, Q46) |
| Registration requests (D74) | — | M (approve/reject) | submits one |
| Identity documents (restricted) | — | V (Q44, Q62) | type and masked number only, no file (Q62) |
| Allocation | — | M | — (D79) |
| Fees & payments | — | M (approves refunds; the sole Administrator's own approval is marked self-approved, D76) | own (submits UTR, Q28) |
| Leave | — | M (approve) | own (request, cancel) |
| Complaints | — | M | own (submit/reopen) |
| Announcements | — | M | V |
| Mess menu | — | M | V |
| Home overview (D86) | organization counts | M (counts) | — |
| Audit logs (read-only) | V, all orgs, personal data masked (Q17, Q55) | V, own org (Q17) | — |

### 2.3 Non-functional requirements

| ID | Category | Requirement | Source |
|---|---|---|---|
| NFR-1 | Tenant isolation | Organization identifier on every organization-owned record; organization-scoped queries; backend authorization; tenant-aware ownership; PostgreSQL Row-Level Security; automated cross-tenant tests; never trust organization IDs sent by the client. | §7 |
| NFR-2 | Security | Secure password hashing, short-lived JWTs plus refresh tokens, session invalidation, login rate limiting, input validation, secure uploads, protection against common web attacks (OWASP Top 10). | §8 |
| NFR-3 | Auditability | Comprehensive, tamper-resistant audit trail; secrets never logged; read access limited to the Administrator and the Platform Owner (Q17). | §9 |
| NFR-4 | Data integrity | No double bed allocation; financial history append-only (reversals only, Q31); gap-free receipt numbers (Q29); audited corrections. | §6.4–6.6, §16 |
| NFR-5 | Recoverability | Daily backups, PITR, verified backups, tested restores. RPO ≤ 5 min / RTO ≤ 4 h for a future hosted environment; local best-effort (DECIDED Q4). Backups on a second drive/external disk (Q5). | §10 |
| NFR-6 | Cost | ₹0. Free/open-source only; every free-tier limit flagged; paid items need approval. No domain purchase (DECIDED Q2). | §3 |
| NFR-7 | Local-first | Local Docker Compose only; deployment out of scope for now, but nothing may block hosting later (DECIDED Q1). Windows + Docker Desktop. Moving the project into the WSL2 filesystem: ask again right before scaffolding (DECIDED Q7). | §3, §12 |
| NFR-8 | Maintainability | Modular monolith, no unnecessary technologies, clear module boundaries. Swappable edges: email sender, file storage, (future) payment gateway. | §12, §17 |
| NFR-9 | Testability | pytest, FastAPI TestClient, database integration tests, frontend tests, cross-tenant security tests. | §7, §12 |
| NFR-10 | Usability | Premium modern SaaS look: sidebar navigation, light/dark themes, responsive desktop and mobile layouts, clean tables with search and filters, subtle animation, one design system. | §13 |
| NFR-11 | Scalability | No fixed limit on hostels. Design target: 50 organizations, up to 5,000 students per organization, ~500 concurrent users in total (DECIDED Q3). | §15 |
| NFR-12 | Performance | Not stated. PROPOSAL: p95 under 500 ms for normal API calls and dashboards under 2 s at the target scale. | — |
| NFR-13 | Accessibility | Not stated. PROPOSAL: keyboard navigation, visible focus, WCAG AA colour contrast in both themes. | — |
| NFR-14 | Privacy | Assume some residents may be minors; collect the minimum; identity documents restricted to the Administrator; masked Aadhaar only; retention and deletion policy before any production use (DECIDED Q44; not legal advice). | — |
| NFR-15 | Localization | INR, English; timestamps in UTC; organization timezone (default Asia/Kolkata); dates DD-MM-YYYY (DECIDED Q48). | — |
| NFR-16 | Observability | Platform health page: API/DB status, job queue and failures, email sent vs daily cap, last backup, last restore test, disk usage, error rate (DECIDED Q50). | §4A |
| NFR-17 | Source control | Git, with the code on GitHub (D90). Each change ends with the local quality gate (lint, types, tests, dependency audit, secret scan); there is no hosted CI. Installing packages from npm, PyPI and Docker Hub is fine. |

---

## Part 3 — Gaps, ambiguities, conflicts and dependencies

### 3.1 Missing requirements

These are things the system needs that the brief doesn't mention, and how each was resolved.

| # | Missing | Why it matters | Resolution |
|---|---|---|---|
| M1 | **How login knows the organization** | Student ID and Employee ID are unique only *within* an organization. | DECIDED (Q8): unique slug; `/o/{org-slug}/login`. |
| M2 | **Identity scope** | Same person or email in two organizations. | DECIDED (Q9): accounts belong to one organization; email unique within it. |
| M3 | **Academic session / term** | Allocations, fee cycles and reports follow academic years. | DECIDED (Q18): organization-defined sessions. |
| M4 | **Hostel type / gender rules** | Wrong boys/girls allocation is a serious real-world error. | DECIDED (Q19): resident type enforced at allocation. |
| M5 | **Room types and pricing** | Fees usually depend on room type. | DECIDED (Q20): room-type entity that fees can target. |
| M6 | **Student exit lifecycle** | Graduation, withdrawal, settlement, deactivation. | DECIDED (Q22): settlement → 90 days read-only → deactivated. |
| M7 | **Discounts, waivers, scholarships, fines** | They change balances. | DECIDED (Q27): adjustment entries with reason. |
| M8 | **Proration** | Joining mid-cycle or changing rooms. | DECIDED (Q25): manual adjustment, no automatic proration. |
| M9 | **Late-fee formula** | "Late fees" alone cannot be implemented. | DECIDED (Q26): grace days + flat or capped per-day amount. |
| M10 | **Receipt numbering** | Accounting needs sequential, gap-free numbers. | DECIDED (Q29): per organization per financial year; no GST. |
| M11 | **Refund approval** | Refunds move money out. | DECIDED (Q30): maker-checker. |
| M13 | **Leave types** | Day outings vs home leave. | DECIDED (Q35). |
| M15 | **Suspended organization behaviour** | Logins, data, jobs. | DECIDED (Q14). |
| M16 | **Platform Owner data access** | §7 says "explicitly authorized" without defining it. | DECIDED (Q15); audit logs visible with personal data masked (Q17, Q55). |
| M17 | **Self-registration abuse** | Spam/fake organizations. | DECIDED (Q13): rate limit + email verification + suspension. |
| M18 | **Students without email / failed email** | Activation is email-only. | DECIDED (Q2), **changed by D80:** every student has an email; the Administrator can send the activation email again. |
| M20 | **Session and password policy** | Token lifetimes, "log out all devices", password rules. | DECIDED (Q67, Q71): session lifetimes as in 04 §5.6; NIST SP 800-63B-4 password rules as in 04 §5.11 (at least 8 characters, a blocklist, no composition rules). |
| M21 | **Data retention and privacy** | Identity documents, possible minors, deletion requests. | DECIDED (Q44): minimal data, restricted access, masked Aadhaar; retention policy before production. |
| M22 | **Bulk import** | Thousands of beds can't be entered by hand. | DECIDED (Q23): rooms/beds CSV in v1; students later. |
| M23 | **Export formats** | "Reports" without formats. | DECIDED (Q47): CSV; PDF receipts. |
| M24 | **Notification preferences** | Muting; also reduces email volume. | DECIDED (Q49). |
| M25 | **Mess and audit-log ownership** | No role responsible in §4. | DECIDED (Q17, Q41, Q59). |
| M26 | **Timezone and "day" boundaries** | Leave dates, due dates. | DECIDED (Q48): UTC storage; organization timezone. |

### 3.2 Ambiguities

| # | Brief says | Unclear because | Resolution |
|---|---|---|---|
| A1 | §6.3 "Admin selects an organization and hostel" | An organization admin only ever belongs to one organization. | PROPOSAL (reading of the brief): organization comes from the admin's login; the admin selects only the hostel. The Platform Owner does not admit students. |
| A2 | §6.4 "Authorized Admins" allocate | Which admins? | The Administrator (D75). |
| A3 | §6.5 "Online payment support" + "do not assume a gateway is free" | Online payment *flow* or *recording* only? | DECIDED (Q28): UPI link + student-submitted UTR, verified by the Administrator; no gateway. |
| A9 | §4A "Monitor platform health" | Which signals? | DECIDED (Q50). |
| A10 | §6.5 billing cycles, "student-specific charges where appropriate" | Which cycles; what is "appropriate"? | DECIDED (Q24, Q27). |
| A11 | §11 "Payment receipts" as stored files | Generated receipts or uploaded proofs? | DECIDED (Q28, Q47): generated receipts rendered from ledger data (PDF); student payment proofs stored as uploads. |
| A13 | §6.2 "Hostel capacity" | Manual or derived? | PROPOSAL: derived from active beds (HST-2). |
| A14 | §6.4 "Reserved beds" | For whom, until when? | DECIDED (Q21). |

### 3.3 Conflicts and tensions

| # | Conflict | Status | Resolution |
|---|---|---|---|
| C1 | **Email is mandatory, and free email is limited.** Registration, activation, reset, OTP and notifications all depend on email, and free tiers cap volume and need a paid domain. | **Resolved for now** | Gmail SMTP with an app password when configured (about 500 a day, no domain needed), Mailpit otherwise; test addresses always go to Mailpit (D88). Sign-in never needs email; only a forgotten password does. |
| C2 | **"Online payment support" vs ₹0** | **Resolved** | Gateway-independent accounting; UPI link + UTR verification (DECIDED Q28). |
| C3 | **"Production-oriented" vs no paid hosting** | **Resolved for now** | Deployment out of scope; local Docker Compose; nothing may block hosting later (DECIDED Q1). |
| C4 | **"Local storage" backups vs disaster recovery** | **Resolved** | Backup repository on a second drive/external disk (DECIDED Q5). Still no off-site copy; acceptable while local-only. |
| C5 | **Files outside the database vs PITR** | Design approach | Write-once files + a separate file backup (02 §12). |
| C8 | **Immutable audit logs vs privacy deletion rights** | **Partly resolved** | Retention policy before production (DECIDED Q17, Q44); store IDs and minimal values in audit records. |
| C9 | **"Record request metadata" vs "never log secrets"** | Design approach | Allowlist of recorded metadata (IP, user agent, request ID). |
| C10 | **Shared tables + RLS vs Platform Owner access** | Design approach | Separate, audited platform-only database access path (02 §8). |
| C11 | **§12 lists GitHub vs local-only** | **Resolved** | The code is on GitHub (D90); the app itself runs locally. Checks run locally with the quality gate, not GitHub Actions. |
| C12 | **"No parent portal" vs typical hostel practice** | **Resolved** | No automatic guardian messages in v1 (DECIDED Q36). |
| C13 | **Platform Owner reads all audit logs (Q17) vs sees no student personal data (Q15)** | **Resolved** | Personal data masked by default in the Platform Owner's audit view (DECIDED Q55). |

### 3.4 Dependencies between modules

Modules must be built bottom-up. Each layer depends on the layers above it in this list:

```
L0  Foundation (cross-cutting, build first)
    tenancy & org context · auth (login, tokens) · RBAC & hostel scopes
    audit log · file storage · background jobs/outbox · notifications (in-app + email sender)
L1  Organization management (registration, slug, settings, academic sessions, suspension)
L2  Hostel structure (hostel → building → floor → room → bed, room types)
L3  Student admission (needs hostel, files incl. mandatory photo, activation link)
L4  Room & bed allocation (needs beds + students)
L5  Fees & payments        Leave        Complaints        Mess
L6  Home overview (counts)
```

| Module | Depends on | Why |
|---|---|---|
| Everything | Tenancy, auth, RBAC, audit | Every request needs an organization context and a permission check; every important write needs an audit record. Retrofitting these later is expensive and risky. |
| Student admission | Hostels, file storage, email sender (activation links) | Admission chooses a hostel, uploads documents (photo mandatory) and activates the account. |
| Allocation | Beds, students, academic sessions | Links one to the other within a session; checks resident type. |
| Fees | Students, room types, allocation, academic sessions | Charges depend on student, room type and session. |
| Leave | Students, notifications | Approval flow. |
| Complaints | Students, staff, rooms, files | Assignment and attachments. |
| Mess | Hostels | Mostly independent, so a low-risk module that can be built at any time. |
| Home overview | Students, beds, requests, leave, complaints | Counts only (D86). |

Two dependency points to note now:

1. **History is kept from day one.** Questions like "who was in this bed in August" can only be answered if we keep history (allocations with start and end dates, dated ledger entries, leave records) instead of overwriting the current state. History cannot be recovered later, so the database design (next phase) must store it from the start.
2. **Notifications are needed by almost everything but should block nothing.** Modules should only *emit* "something happened" events. The notification module decides what to send and how. This keeps modules independent, and the email sender interface can later be connected to a real provider without touching them.
