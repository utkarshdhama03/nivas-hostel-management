#!/usr/bin/env bash
# Demo data for presentations (README "Demo data").
#   ./scripts/demo-data.sh create    remove earlier demo data, then build it again (about 8 minutes)
#   ./scripts/demo-data.sh reset     remove the demo data only
#   ./scripts/demo-data.sh status    say what exists
#
# It builds two sample organizations (demo-uni, sunrise) and a demo Platform Owner in the
# development database, through the application's own API and services (backend/tests/demo/seed.py).
# The logins go to demo-logins.md in the secrets folder, outside the project.
#
# Reset touches only those two organizations and that Platform Owner, and refuses an organization on
# those addresses that the seed didn't make.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1

secrets_dir() {  # the path from .env (a folder name, not a secret)
  grep -E '^HMS_SECRETS_DIR=' .env | head -1 | cut -d= -f2- | tr -d '"\r'
}

command="${1:-}"
case "$command" in
  create|reset|status) ;;
  *) echo "usage: ./scripts/demo-data.sh create | reset | status"; exit 2 ;;
esac

docker compose up -d --wait db
out="$(secrets_dir)"
[ -n "$out" ] || { echo "HMS_SECRETS_DIR is not set in .env"; exit 1; }

if [ "$command" = "status" ]; then
  docker compose run --rm -T tools python -m tests.demo.seed status
  exit 0
fi

# The worker acts on data in real time (late fees, closing old complaints, expiring reservations),
# which would disturb history while it is being replayed. Stop it, and start it again whatever happens.
worker_was_running="$(docker compose ps -q worker 2>/dev/null || true)"
restart_worker() { [ -z "$worker_was_running" ] || docker compose start worker >/dev/null 2>&1 || true; }
trap restart_worker EXIT
[ -z "$worker_was_running" ] || docker compose stop worker >/dev/null

docker compose run --rm -T tools alembic upgrade head
docker compose run --rm -T -v "$out:/demo-out" tools python -m tests.demo.seed "$command" --out /demo-out

# Leftovers of earlier versions (authenticator pictures and keys; D87 removed two-step verification).
rm -f "$out"/demo-totp-*.png "$out"/demo-totp.json
if [ "$command" = "create" ]; then
  echo
  echo "Logins (emails and the passwords from demo-passwords.env): $out/demo-logins.md"
else
  rm -f "$out"/demo-logins.md
fi
