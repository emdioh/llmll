#!/usr/bin/env bash
# Rebuild all memory projections from the event log (e.g. after changing projection parameters).
source "$(dirname "$0")/_lib.sh"
require uv
load_env
migrate_db
(cd "$BACKEND" && uv run python -m app.cli replay)
