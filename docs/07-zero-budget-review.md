# 07 — Zero-Budget Review

| | |
|---|---|
| Status | Covers everything named in docs 02–06. New questions are in §10. |
| Date | 2026-11-07 |
| Budget | ₹0 (brief §3) |

**Legend:** **Free** = no cost and no meaningful condition for us. **Condition** = free for us, but with a licence condition or usage limit worth knowing. **Paid tier** = the product also sells paid plans; we must stay on the free part. **Not used** = mentioned in the design for later, deliberately not used now.

**About the licences:** licences are given as published by each project, with the checks cited in §11, as of November 2026. Projects occasionally change licences, so each one is re-checked when it's actually installed (Phase 0 onward). Q81 proposes doing that automatically.

---

## 1. Summary

- **Every component needed to build, run and test the system locally costs ₹0** and is open source, with one exception: **Docker Desktop**, which is proprietary but free for personal and educational use (§3).
- **Things to watch** (none cost money in our current situation):
  - Docker Desktop's licence terms if development moves inside a large organization or a government body (§3);
  - Docker Hub pull limits (§6);
  - psycopg's LGPL licence (§4.1);
  - Tailwind's paid add-on kit, which we must not use (§5.1);
  - MinIO's AGPL licence if we ever use it (§8).
- **Everything that would cost money is out of scope:** hosting, a domain, a real email provider, a payment gateway, SMS/WhatsApp (§8).

---

## 2. Runtimes and platform

| Item | Used for | Licence | Cost | Flag |
|---|---|---|---|---|
| Windows 11 | Development machine | Proprietary (already owned) | ₹0 | — |
| WSL2 (+ Ubuntu) | Linux environment for Docker | WSL: free Windows feature, open-sourced by Microsoft in 2025 (MIT); Ubuntu: free (mixed open-source licences) | ₹0 | — |
| Python 3.13 (Q77) | Backend runtime | PSF License 2.0 | ₹0 | — |
| Node.js 24 LTS (Q77) | Frontend build tooling | MIT | ₹0 | — |
| PostgreSQL 18 | Database | PostgreSQL License (permissive) | ₹0 | — |
| `btree_gist` | Exclusion constraints | Part of PostgreSQL | ₹0 | — |
| Git | Version control; the code is on GitHub (D90) | GPL-2.0 (we only use the tool) | ₹0 | Git Bash also runs the scripts |

## 3. Docker

| Item | Licence | Cost for us | Flag |
|---|---|---|---|
| Docker Engine (Moby) | Apache-2.0 | ₹0 | — |
| Docker Compose v2 | Apache-2.0 | ₹0 | — |
| **Docker Desktop** | **Proprietary**, under the Docker Subscription Service Agreement | ₹0 **for personal / educational use** | **Condition** (below) |

**Docker Desktop licence terms** (from Docker's own documentation, checked 2026-11-07):

- **Free for:** personal use; education; non-commercial open-source projects; small businesses with **fewer than 250 employees AND less than US$10 million** annual revenue.
- **Paid subscription required** (Docker Pro, Team or Business) for: professional use in larger organizations; **government entities**; commercial use beyond those limits.
- Docker's page doesn't show an effective date, so these should be re-checked if circumstances change.

**What this means for this project:**

| Situation | Docker Desktop cost |
|---|---|
| Developing it as a student project on a personal laptop | **Free** (personal / educational use) |
| A future contributor working for a company with 250+ employees or $10M+ revenue | That company needs a paid seat for them |
| Development done by staff of a **government entity**, which many Indian public universities are | Paid seats needed |
| Running the system for hostels (servers, end users) | **Never needed.** Servers run Docker Engine (Apache-2.0); end users only use a browser. |

**Free way out if a condition ever applies:** install Docker Engine + the Compose plugin directly inside WSL2 Ubuntu, with no Docker Desktop at all. Our Compose files work unchanged. This is Q82, decided together with Q7 at the start of Phase 0.

## 4. Backend

### 4.1 Runtime libraries (Python)

| Library | Used for | Licence | Flag |
|---|---|---|---|
| FastAPI | Web framework | MIT | — |
| Starlette | FastAPI's foundation | BSD-3-Clause | — |
| Uvicorn | ASGI server | BSD-3-Clause | — |
| Pydantic, pydantic-settings | Validation, configuration | MIT | — |
| SQLAlchemy 2.0 | Database access | MIT | — |
| Alembic | Migrations | MIT | — |
| **psycopg 3** | PostgreSQL driver | **LGPL-3.0** | **Condition:** using it as a library is fine. Only if we *modified psycopg itself* and distributed it would we have to share those changes. No cost. |
| python-multipart | File uploads | Apache-2.0 | — |
| PyJWT | Access tokens | MIT | — |
| argon2-cffi | Password hashing | MIT | — |
| cryptography (Q65) | AES-256-GCM encryption | Apache-2.0 OR BSD-3-Clause | — |
| Jinja2 | Email and receipt templates | BSD-3-Clause | — |
| Pillow | Image checking and re-encoding | MIT-CMU (HPND, permissive) | — |
| WeasyPrint (Q75) | PDF receipts | BSD-3-Clause | Installed in Phase 5 with Debian's Pango, HarfBuzz and DejaVu fonts in the backend image (LGPL and free fonts; used unmodified, no cost) |
| Python standard library (`hmac`, `secrets`, `hashlib`) | Token signing (HMAC), random tokens | PSF | — |

### 4.2 Development and test tools (Python)

| Tool | Used for | Licence | Network use | Flag |
|---|---|---|---|---|
| uv (Q77) | Dependency management and lockfiles | MIT OR Apache-2.0 | Downloads packages from PyPI | — |
| pytest, httpx (TestClient) | Tests | MIT, BSD-3-Clause | — | — |
| ruff | Lint and format | MIT | — | — |
| mypy | Type checks | MIT | — | — |
| pip-audit (Q73) | Known-vulnerability check | Apache-2.0 | Queries PyPI's public vulnerability data (free, no account) | — |
| pip-licenses (Q81, if accepted) | Licence listing | MIT | — | — |
| Faker (Q79, accepted) | Synthetic names for the Phase 10 scale test (`backend/tests/scale/`) | MIT | — | Development only |
| time-machine 3.6 (D67) | Freezes the clock in backend tests; the database follows through `app.now` | MIT | — | Development only |
| import-linter (optional) | Module boundary check | BSD-2-Clause | — | Only if needed (02 §4.1) |

## 5. Frontend

### 5.1 Runtime libraries (npm)

| Library | Used for | Licence | Flag |
|---|---|---|---|
| React, React DOM | UI | MIT | — |
| React Router | Routing | MIT | — |
| TypeScript | Types | Apache-2.0 | — |
| **Tailwind CSS** | Styling | MIT | **Paid tier exists:** "Tailwind Plus" (a paid component/template kit). We use only open-source Tailwind and shadcn/ui. |
| shadcn/ui | Component code copied into the repo | MIT | — |
| Radix UI primitives | Accessible building blocks | MIT | — |
| lucide-react | Icons | ISC | — |
| TanStack Query, TanStack Table | Data fetching, tables | MIT | — |
| React Hook Form, Zod | Forms and validation | MIT | — |
| qrcode (node-qrcode) | Drawing the UPI payment code | MIT | — |
| Inter font (via Fontsource) | Typography, self-hosted | Font: SIL OFL 1.1; package: MIT | No Google Fonts calls at runtime |
| @hookform/resolvers, class-variance-authority, clsx, tailwind-merge | Form/validation glue, component variants, class merging | MIT / Apache-2.0 (cva) | Added in Phase 1 |

### 5.2 Development and test tools

| Tool | Used for | Licence | Network use | Flag |
|---|---|---|---|---|
| Vite | Dev server and build | MIT | — | — |
| openapi-typescript | Types from the API schema | MIT | — | — |
| ESLint | Lint | MIT | — | — |
| Vitest, React Testing Library, jsdom | Frontend tests | MIT | — | — |
| Playwright (Q78) | End-to-end tests | Apache-2.0 | Downloads test browsers from Microsoft's servers (free, no account) | Browsers carry their own open-source licences (Chromium BSD, Firefox MPL-2.0, WebKit LGPL/BSD). Phase 1: only Chromium, installed into the `hms-e2e` image built from `node:24-slim` (Q87) |
| @types/qrcode | Type definitions | MIT | — | Added in Phase 1 |
| npm CLI + `npm audit` (Q73) | Packages, vulnerability check | Artistic-2.0 | npm registry (operated by GitHub; allowed under Q6) | — |
| license-checker (Q81, if accepted) | Licence listing | BSD-3-Clause | — | — |

## 6. Docker images

| Image (source: Docker Hub) | Contents | Licence | Flag |
|---|---|---|---|
| `postgres:18` (Docker Official Image) + pgBackRest (from the PostgreSQL apt repository) | Database + backups. The backup scripts (Phase 10) also use `openssl` (Apache-2.0) and `gosu` (Apache-2.0), both already in the image: nothing new installed | PostgreSQL License; pgBackRest MIT; Debian base (mixed open source) | — |
| `python:3.13-slim` (official) | API and worker | PSF + Debian | — |
| `node:24-slim` (official) | Frontend build/dev | MIT + Debian | — |
| `axllent/mailpit` | Local email catcher | MIT | Development only; never delivers mail |
| `hms-e2e` (built locally from `node:24-slim` + Playwright Chromium) | End-to-end tests (Q87) | MIT + Chromium BSD | Development only |
| `zricethezav/gitleaks` (Q73) | Secret scanning, run locally | MIT | Pulled from Docker Hub, not GitHub's registry. Gitleaks' *GitHub Action* needs a licence key for organizations, but we don't use GitHub. |

**Docker Hub pull limits** (Docker documentation, checked 2026-11-07): **100 pulls per 6 hours per IP address** without logging in; **200 per 6 hours** with a free Personal account. **Condition:** enough for one developer (images are cached locally after the first pull). If it's ever hit, log in with a free account.

## 7. Developer tools on Windows

| Tool | Licence | Cost | Flag |
|---|---|---|---|
| VS Code | Microsoft's free build (Microsoft licence; source is MIT) | ₹0 | Telemetry can be switched off |
| VS Code WSL extension | Proprietary, free (Microsoft) | ₹0 | Only if the project moves into WSL2 (Q7) |
| Windows Task Scheduler | Part of Windows | ₹0 | For local backup scheduling |
| UIDAI website (masked Aadhaar download) | Government service, free | ₹0 | Used by organizations, not by our system |

## 8. Services and items deliberately **not** used (costs or conditions)

| Item | Status | Cost / condition | Decision |
|---|---|---|---|
| Production hosting | Not used | Real servers cost money; free tiers sleep, wipe disks, lack PITR, or need card verification | Out of scope (Q1) |
| Domain name | Not used | Yearly fee | Not purchased (Q2) |
| TLS certificates (Let's Encrypt) | Not used yet | Free | Only when hosted |
| Caddy (reverse proxy) | Not used yet | Apache-2.0, free | Only when hosted |
| Email providers | **Gmail SMTP used when configured (D88)** | Free personal account with an app password (needs Google 2-Step Verification on that account); about 500 messages a day; no domain needed. Brevo 300/day; Resend 100/day + needs a domain; SendGrid has no free plan (02 B7) | Gmail for real addresses, Mailpit for test addresses and when Gmail isn't configured |
| Payment gateways | Not used | Per-transaction fees + business KYC | UPI links + UTR verification instead (Q28) |
| SMS / WhatsApp | Not used | Paid per message | Deferred |
| CAPTCHA services | Not used | Free tiers, third-party | Not needed (Q13) |
| Cloud backup storage | Not used | Free tiers small; privacy concerns | External disk (Q5) |
| ClamAV | Not used | GPL-2.0, free, but ~1 GB+ RAM | Not in v1 (Q45) |
| Error-tracking SaaS (e.g. Sentry) | Not used | Free tiers with quotas | Logs + health page instead |
| **MinIO** (possible future S3-compatible storage) | Not used | **AGPL-3.0**: free to run, but with network-use obligations if modified; its distribution terms have been changing, so re-check if ever needed | Local filesystem storage (Q11 of brief); if ever needed, also consider Apache-2.0 alternatives |
| `pg_trgm` | Not used | Free (part of PostgreSQL) | Only if search gets slow, with approval |
| Hash-chained audit log | Not used | Would be free (our own code) | Deferred (Q74) |
| GitHub | Hosts the source code (D90), free for public repositories | — | Only the code; GitHub Actions, Dependabot and GitHub secret scanning are not used (checks run locally, Q73) |

## 9. Data files bundled with the app

| Data | Source | Licence | Flag |
|---|---|---|---|
| Common-password blocklist, top 100,000 (Q71) | SecLists project | MIT | One-time download, stored in the project (Q80) |
| Fonts | Fontsource / Inter | SIL OFL 1.1 | Self-hosted |

## 10. Decisions from this document

- **Q80.** The SecLists password list is downloaded once and stored in the project, with its source and licence (`backend/app/core/data/SOURCE.md`).
- **Q81.** A licence check runs in the quality gate as a warning, not a failure.
- **Q82.** Docker Desktop with the WSL2 backend.

## 11. Sources (checked 2026-11-07)

- Docker Desktop licence terms: [docs.docker.com — Docker Desktop license agreement](https://docs.docker.com/subscription/desktop-license/)
- Docker Hub pull limits: [docs.docker.com — Docker Hub usage and limits](https://docs.docker.com/docker-hub/usage/pulls/)
- NIST SP 800-63B-4 password rules (Q71), via secondary summaries: [Netwrix](https://netwrix.com/en/resources/blog/nist-password-guidelines/), [Deepak Gupta](https://guptadeepak.com/ebooks/passwordless-playbook-for-brands/the-new-password-rules/). Primary text: [pages.nist.gov/800-63-4/sp800-63b.html](https://pages.nist.gov/800-63-4/sp800-63b.html#passwordver)
- Email provider limits: see 02 Sources.
