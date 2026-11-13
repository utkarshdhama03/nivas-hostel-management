#!/bin/sh
# Starts the Vite dev server in the web container (compose.yaml).
# Installs packages only when node_modules (a named volume that survives "docker compose down")
# doesn't match package-lock.json, so a plain restart needs no internet (review Critical 6).
# "docker compose down -v" or a changed lockfile installs again.
set -e
want=$(sha1sum package-lock.json | cut -d' ' -f1)
have=$(cat node_modules/.lock-hash 2>/dev/null || true)
if [ "$have" = "$want" ]; then
  echo "node_modules matches package-lock.json: no install needed"
else
  npm ci --no-audit --no-fund
  echo "$want" > node_modules/.lock-hash
fi
exec npm run dev -- --host 0.0.0.0 --port 5173
