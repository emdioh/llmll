#!/usr/bin/env bash
# First-time setup for local development: install dependencies, create the database,
# import the curriculum. Safe to re-run.
source "$(dirname "$0")/_lib.sh"
require uv npm
load_env

step "Backend dependencies"
(cd "$BACKEND" && uv sync)

step "Frontend dependencies"
(cd "$FRONTEND" && npm ci)

step "Database migrations"
(cd "$BACKEND" && uv run alembic upgrade head)

step "Curriculum import"
(cd "$BACKEND" && uv run python -m app.cli import-curriculum --path "$CURRICULUM")

[[ -f "$ROOT/.env" ]] || echo -e "\nTip: cp .env.example .env and add an API key (without one the LLM is simulated)."
echo -e "\nDone. Start developing with: scripts/dev.sh"
