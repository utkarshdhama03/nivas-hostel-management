#!/usr/bin/env bash
# =============================================================================
# Local quality gate (docs/02 §14, docs/06 §4). Replaces GitHub Actions (Q6).
# Run from the project root in Git Bash:   ./scripts/check.sh
#
# Everything runs inside the project's containers, so results don't depend on
# what is installed on the Windows host.
#   FAIL  = the gate fails (exit code 1)
#   WARN  = reported only (licence findings, Q81)
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1          # stop Git Bash rewriting container paths like /repo

failed=()
step() { echo; echo "=== $1"; }
run()  { local name="$1"; shift; step "$name"; if "$@"; then echo "PASS  $name"; else echo "FAIL  $name"; failed+=("$name"); fi; }
warn() { local name="$1"; shift; step "$name (warning only)"; "$@" || echo "WARN  $name reported findings"; }

TOOLS=(docker compose run --rm tools)
WEB=(docker compose run --rm --no-deps web)

step "start the database"
docker compose up -d --wait db || { echo "FAIL  database did not start"; exit 1; }

# --- Backend ----------------------------------------------------------------------------
run "ruff lint"            "${TOOLS[@]}" ruff check .
run "ruff format"          "${TOOLS[@]}" ruff format --check .
run "mypy"                 "${TOOLS[@]}" mypy
run "pytest (incl. database proof suite)" "${TOOLS[@]}" pytest
run "pip-audit"            "${TOOLS[@]}" pip-audit --strict --progress-spinner off
warn "python licences"     "${TOOLS[@]}" sh -c 'pip-licenses --format=plain --order=license \
  --fail-on="GNU General Public License v3 (GPLv3);GNU Affero General Public License v3 (AGPLv3);Proprietary License" \
  --partial-match'

# --- Frontend ---------------------------------------------------------------------------
run "API types up to date" ./scripts/gen-api.sh --check
# A clean install from the lockfile; then record its hash, so the dev server's next start
# (frontend/scripts/start-dev.sh) knows node_modules is current and needs no internet.
run "frontend install"     "${WEB[@]}" sh -c "npm ci --no-audit --no-fund && sha1sum package-lock.json | cut -d' ' -f1 > node_modules/.lock-hash"
# That reinstall happened under a running dev server, if there is one: restart it so the open
# browser keeps working (its pre-bundled dependencies would otherwise be stale).
if [ -n "$(docker compose ps -q web 2>/dev/null)" ]; then docker compose restart web >/dev/null 2>&1; fi
run "typescript"           "${WEB[@]}" npm run -s typecheck
run "eslint"               "${WEB[@]}" npm run -s lint
run "vitest"               "${WEB[@]}" npm test --silent
run "npm audit"            "${WEB[@]}" npm audit --audit-level=high
warn "npm licences"        "${WEB[@]}" npx --yes license-checker@25.0.1 --production --excludePrivatePackages --summary \
  --failOn "GPL-3.0;AGPL-3.0;SSPL-1.0;UNLICENSED"

# --- Secrets ----------------------------------------------------------------------------
run "gitleaks (working tree)" docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/repo" -w /repo \
  zricethezav/gitleaks:v8.30.1 dir /repo --config /repo/.gitleaks.toml --redact --no-banner

echo
echo "(End-to-end flows are separate: ./scripts/e2e.sh, with the stack running.)"
if [ ${#failed[@]} -eq 0 ]; then
  echo "QUALITY GATE: PASS"
else
  echo "QUALITY GATE: FAIL -> ${failed[*]}"
  exit 1
fi
