#!/usr/bin/env bash
# Build and start the full app with Docker (app + LanguageTool) on http://localhost:8000.
# Usage: scripts/up.sh [logs|down]
source "$(dirname "$0")/_lib.sh"
require docker
cd "$ROOT"
case "${1:-}" in
  logs) exec docker compose logs -f app ;;
  down) exec docker compose down ;;
  "")
    [[ -f .env ]] || echo "warning: no .env (copy .env.example): the LLM will be simulated"
    docker compose up -d --build
    echo "starting… http://localhost:8000   (logs: scripts/up.sh logs, stop: scripts/up.sh down)"
    ;;
  *) echo "usage: $0 [logs|down]" >&2; exit 1 ;;
esac
