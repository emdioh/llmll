#!/usr/bin/env bash
# Validate and import curriculum/de into the local database (idempotent).
# After editing YAML files run this; validation errors are listed as file:index.
source "$(dirname "$0")/_lib.sh"
require uv
load_env
(cd "$BACKEND" && uv run alembic upgrade head >/dev/null && uv run python -m app.cli import-curriculum --path "$CURRICULUM")
