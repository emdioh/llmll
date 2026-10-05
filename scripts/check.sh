#!/usr/bin/env bash
# Run the same checks as CI. Usage: scripts/check.sh [backend|frontend]  (default: both)
source "$(dirname "$0")/_lib.sh"
require uv npm
what="${1:-all}"

if [[ "$what" == all || "$what" == backend ]]; then
  step "Backend: lint, format, tests"
  (cd "$BACKEND" && uv run ruff check . && uv run ruff format --check . && uv run pytest -q)
  step "API schema in sync with the frontend"
  tmp="$(mktemp)"
  (cd "$BACKEND" && uv run python -m app.cli export-openapi "$tmp" >/dev/null)
  if ! diff -q "$tmp" "$FRONTEND/src/api/openapi.json" >/dev/null; then
    rm -f "$tmp"; echo "error: frontend/src/api/openapi.json is stale: run scripts/gen-api.sh" >&2; exit 1
  fi
  rm -f "$tmp"
fi
if [[ "$what" == all || "$what" == frontend ]]; then
  step "Frontend: lint, types, format, tests, build"
  (cd "$FRONTEND" && npm run lint && npm run typecheck && npm run format:check && npm test && npm run build)
fi
echo -e "\nAll checks passed."
