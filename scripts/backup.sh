#!/usr/bin/env bash
# Consistent online backup of the SQLite database into backups/ (uses SQLite's backup API,
# safe while the app is running). Usage: scripts/backup.sh [docker|podman|local]
#   docker / podman: the database in the llmll-data volume, via the compose app container
#   local: backend/data/llmll.db (or LLMLL_DATABASE_URL)
#   no argument: the engine in LLMLL_CONTAINER_ENGINE if its app is running, else the first of
#   docker, podman whose app is running, else local
source "$(dirname "$0")/_lib.sh"
load_env
mkdir -p "$ROOT/backups"
stamp="$(date +%Y%m%d-%H%M%S)"
mode="${1:-}"
if [[ -z "$mode" ]]; then
  mode="$(detect_engine)"
  mode="${mode:-local}"
fi

backup_py='import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
dst.close(); src.close()'

case "$mode" in
  docker|podman)
    compose_cmd "$mode"
    out="$ROOT/backups/llmll-$stamp.db"
    tmp="$out.part"
    trap 'rm -f "$tmp"' EXIT
    cd "$ROOT"
    "${COMPOSE[@]}" exec -T app python -c "$backup_py" /data/llmll.db /data/backup.db
    # `exec cat` instead of `compose cp`, which podman-compose does not have.
    "${COMPOSE[@]}" exec -T app cat /data/backup.db > "$tmp"
    [[ -s "$tmp" ]] || { echo "error: backup copy is empty" >&2; exit 1; }
    mv "$tmp" "$out"
    "${COMPOSE[@]}" exec -T app rm -f /data/backup.db
    ;;
  local)
    require uv
    url="${LLMLL_DATABASE_URL:-sqlite:///./data/llmll.db}"
    db="${url#sqlite:///}"
    [[ "$db" = /* ]] || db="$BACKEND/${db#./}"
    [[ -f "$db" ]] || { echo "error: database not found: $db" >&2; exit 1; }
    (cd "$BACKEND" && uv run python -c "$backup_py" "$db" "$ROOT/backups/llmll-$stamp.db")
    ;;
  *) echo "usage: $0 [docker|podman|local]" >&2; exit 1 ;;
esac
echo "backup: backups/llmll-$stamp.db"
