#!/usr/bin/env bash
# Regenerate the frontend's API types from the backend's OpenAPI document (docs/02 §3).
# A renamed or removed backend field then becomes a TypeScript compile error.
#   ./scripts/gen-api.sh            write frontend/src/api/openapi.json + schema.d.ts
#   ./scripts/gen-api.sh --check    fail if the generated files are out of date (quality gate)
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
docker compose run --rm -T --no-deps tools python -c \
  "import json; from app.main import create_app; print(json.dumps(create_app(run_checks=False).openapi(), indent=2, sort_keys=True))" \
  > "$tmp/openapi.json"
cp "$tmp/openapi.json" "$tmp/openapi.new.json"
mkdir -p "$tmp/out"
docker compose run --rm -T --no-deps -v "$(pwd -W 2>/dev/null || pwd)/frontend:/web" web \
  sh -c "cat > /tmp/openapi.json && npx --no-install openapi-typescript /tmp/openapi.json -o /tmp/schema.d.ts >/dev/null && cat /tmp/schema.d.ts" \
  < "$tmp/openapi.json" > "$tmp/schema.d.ts"

if [ "${1:-}" = "--check" ]; then
  diff -q "$tmp/openapi.json" frontend/src/api/openapi.json >/dev/null && diff -q "$tmp/schema.d.ts" frontend/src/api/schema.d.ts >/dev/null \
    || { echo "API types are out of date: run ./scripts/gen-api.sh"; exit 1; }
  echo "API types up to date"
else
  cp "$tmp/openapi.json" frontend/src/api/openapi.json
  cp "$tmp/schema.d.ts" frontend/src/api/schema.d.ts
  echo "wrote frontend/src/api/openapi.json and schema.d.ts"
fi
