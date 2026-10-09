#!/usr/bin/env bash
# Validate and import curriculum/de (plus the gitignored curriculum-private/de when present)
# into the local database (idempotent). Both folders go in one run, so neither suspends the other.
# After editing YAML files run this; validation errors are listed as file:index.
source "$(dirname "$0")/_lib.sh"
require uv
load_env
migrate_db
curriculum_args
if [[ -d "$CURRICULUM_PRIVATE" ]]; then
  echo "Including private curriculum: curriculum-private/de"
fi
(cd "$BACKEND" && uv run python -m app.cli import-curriculum "${CURRICULUM_ARGS[@]}")
