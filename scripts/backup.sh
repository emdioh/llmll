#!/usr/bin/env bash
# Consistent online backup of the SQLite database into backups/ (uses SQLite's backup API,
# safe while the app is running). Usage: scripts/backup.sh [docker|local]
#   docker (default if the compose app is running): the database in the llmll-data volume
#   local: backend/data/llmll.db (or LLMLL_DATABASE_URL)
source "$(dirname "$0")/_lib.sh"
load_env
mkdir -p "$ROOT/backups"
stamp="$(date +%Y%m%d-%H%M%S)"
mode="${1:-}"
if [[ -z "$mode" ]]; then
  if command -v docker >/dev/null 2>&1 && (cd "$ROOT" && docker compose ps --status running --services 2>/dev/null | grep -qx app); then
    mode=docker
  else
    mode=local
  fi
fi

backup_py='import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
dst.close(); src.close()'

case "$mode" in
  docker)
    require docker
    (cd "$ROOT" && docker compose exec -T app python -c "$backup_py" /data/llmll.db /data/backup.db \
      && docker compose cp app:/data/backup.db "$ROOT/backups/llmll-$stamp.db" \
      && docker compose exec -T app rm -f /data/backup.db)
    ;;
  local)
    require uv
    url="${LLMLL_DATABASE_URL:-sqlite:///./data/llmll.db}"
    db="${url#sqlite:///}"
    [[ "$db" = /* ]] || db="$BACKEND/${db#./}"
    [[ -f "$db" ]] || { echo "error: database not found: $db" >&2; exit 1; }
    (cd "$BACKEND" && uv run python -c "$backup_py" "$db" "$ROOT/backups/llmll-$stamp.db")
    ;;
  *) echo "usage: $0 [docker|local]" >&2; exit 1 ;;
esac
echo "backup: backups/llmll-$stamp.db"
