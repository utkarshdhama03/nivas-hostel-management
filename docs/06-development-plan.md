# 06 — Development Plan

| | |
|---|---|
| Status | Describes how the current product was built and how every change is checked (12 November 2026). |
| First written | 2026-11-07 |

Every phase below is built. The product is one Administrator per organization, its students, and the Platform Owner. Everyone signs in with an email (or ID) and a password. The decision record, including what was removed and why, is the project's decision log.

**How every change runs:**
1. A short plan is written and approved.
2. It is implemented in small, verifiable steps.
3. The quality gate (`./scripts/check.sh`) must pass.
4. The report says what was tested and what wasn't.
5. Before a large or risky change, a dated zip copy of the project goes to a backup folder outside the project. The code is on GitHub (D90).

---

## 1. Build order

| Phase | What it delivered | Demo |
|---|---|---|
| **0** | Scaffolding and database proof: the Docker Compose stack, PostgreSQL 18, the SQL migrations, the database proof tests and the quality gate | — |
| **1** | Platform core: tenant context on every request, organization self-registration with email verification, email and password sign-in, access and refresh tokens with rotation and reuse detection, sessions, rate limits, the audit log, the job worker, email, in-app notifications, Platform Owner sign-in, the generated security suites; the frontend shell with `/admin`, `/portal` and `/platform` | Demo 0 |
| **2** | Organization setup: settings and UPI ID, academic sessions and terms, hostels, buildings, floors, room types, rooms and beds, CSV import of rooms and beds, optimistic locking on every edit | |
| **3** | Students and files: the upload pipeline, encrypted identity documents, admission with an emailed activation link (D80), the exit flow, the student portal profile; later, student registration requests (D74) | |
| **4** | Bed allocation: allocate, reserve, check in, check out, cancel and transfer, the reservation expiry job, and the bed map with its list view | **Demo 1** (minimal working demo) |
| **5** | Fees, payments and the ledger: fee structures, charge runs, dues and deposit balances, cash, UPI and bank payments, gap-free PDF receipts, UPI submissions, fines, waivers, reversals, refunds, late fees | **Demo 2** |
| **6** | Leave: requests, overlap rule, auto-approval of local outings where the hostel allows it, emergency leave, the Administrator's approval | |
| **8** | Complaints (with a reopen window and auto-close), weekly mess menus, announcements and their notifications | |
| **9** | The Administrator's home overview and the Platform Owner console: organizations, suspend and reactivate, health, masked audit log, settings | |
| **10** | Backups, recovery, key rotation and hardening | **Release candidate** |

The phase numbers match the test files (`test_phase8.py`, `phase9.spec.ts`); there is no Phase 7 in the current product.

**Why this order:**
- **Security foundations come first** (Phases 0–1). Tenancy, permissions and audit are needed by every request, and they're expensive and risky to add later.
- **Data follows the hierarchy:** organization → hostel structure → students → beds. Each step needs the one before.
- **Money (5) comes early** because it's the highest-risk business logic, so it got the most review time.
- **Phases 6 and 8 are independent** of each other and of 5.

---

## 2. Phase 0: the database proof suite

These tests run as the **real app roles** against the migrated database (`backend/tests/dbproof/`). They insert data with plain SQL, so they prove the database holds the rules on its own. Test names match 05.

| Area | Proven by |
|---|---|
| Extension and versions | `test_btree_gist_installed_by_owner_without_superuser`, `test_server_is_postgres_18` |
| RLS coverage | `test_every_org_table_has_forced_rls_and_policy`, `test_platform_tables_have_no_app_grants`, `test_app_roles_are_not_owner_superuser_or_bypassrls` |
| RLS behaviour | zero rows without tenant context, other organizations' rows hidden, inserts and updates into another organization refused, the tenant setting cleared after commit |
| Cross-organization references | `test_composite_fk_rejects_cross_org_reference[fk]` (generated for every composite FK) |
| Double booking | `test_bed_overlap_rejected`, `test_student_overlap_rejected`, `test_overlapping_leave_rejected`, and two-connection race tests for beds and students |
| Ledger | sign rules per entry type, no UPDATE or DELETE on financial tables, exact reversals once only, payment entries summing to the amount, concurrent deposit refunds, allocation limits, waivers allocated at commit |
| Receipts | `test_receipt_number_matches_sequence`, `test_receipt_numbers_gap_free_when_payment_fails` |
| Files and documents | `test_storage_key_format_enforced_by_db`, `test_db_rejects_aadhaar_image_or_unattested`, `test_db_rejects_unencrypted_identity_document` |
| Platform window | the platform role has no BYPASSRLS, cannot select masked columns, and only it can suspend an organization |
| Other guarantees | the audit log rejects UPDATE and DELETE; a `version` column on all 19 editable tables |

---

## 3. How every change is checked

| Layer | What | Where |
|---|---|---|
| Database proof tests | Constraints, triggers, RLS, grants | `backend/tests/dbproof/` |
| Unit tests | Pure logic (allocation ordering, late-fee maths, password rules, token signing) | `backend/tests/` |
| API integration tests | FastAPI TestClient against real PostgreSQL | `backend/tests/test_*.py` |
| Generated security suites | Every route classified; cross-tenant and permission tests generated from the route table | `backend/tests/security/` |
| Concurrency tests | Two connections released together by a barrier | allocation and ledger tests |
| Frontend tests | Vitest + React Testing Library | `frontend/src/**/*.test.tsx` |
| End-to-end | Playwright browser flows on their own database (`./scripts/e2e.sh`) | `frontend/e2e/` |

**The quality gate** (`./scripts/check.sh`) runs ruff, ruff format, mypy, pytest, pip-audit, the OpenAPI drift check, `npm ci`, tsc, eslint, vitest, npm audit and a gitleaks scan of the whole folder. Tests run on a frozen clock (`backend/tests/conftest.py`).

**Definition of done for a change:**
- The quality gate is green.
- No unclassified routes.
- Every 05 risk in scope has its test passing.
- Docs are updated: 01–05 for any decision or design change, plus the change log in 03 §13 for the schema.

## 4. Demos

The current demo script, 10 to 12 minutes from the landing page, is [reports/demo-script.md](reports/demo-script.md). The demo data comes from `./scripts/demo-data.sh create`.

## 5. Risks to the plan

| Risk | Mitigation |
|---|---|
| Parts of 03 don't run as written | Phase 0 fixed the spec early and logged every change in 03 §13 |
| Windows/WSL2/Docker setup friction | Decided first (Q7, Q82); the stack starts with one `docker compose up` |
| Ledger complexity | Database proof tests and property-style tests before any screen depended on it |
| Scope creep | Each change starts with an approved plan; anything new goes to the project's decision log |

## 6. Decisions from this document

- **Q75.** WeasyPrint renders the PDF receipts. Decided.
- **Q77.** Python 3.13, Node.js 24 LTS, PostgreSQL 18, `uv` and npm. Decided.
- **Q78.** Playwright for a few critical browser flows. Decided.
- **Q79.** Faker, as a development dependency only, for the scale test. Decided.
