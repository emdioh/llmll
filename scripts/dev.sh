#!/usr/bin/env bash
# Run backend (http://localhost:8000, auto-reload) and frontend dev server
# (http://localhost:5173, proxies /api to the backend). Ctrl+C stops both.
source "$(dirname "$0")/_lib.sh"
require uv npm
load_env

migrate_db

trap 'trap - EXIT INT TERM; kill 0 2>/dev/null' EXIT INT TERM
(cd "$BACKEND" && uv run uvicorn --factory app.main:create_app --reload --port 8000) &
(cd "$FRONTEND" && npm run dev) &
wait
