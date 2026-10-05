# Shared helpers for the scripts in this folder. Source it, don't run it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
CURRICULUM="$ROOT/curriculum/de"

# Load .env into the environment (JSON values in .env are single-quoted, so this is safe).
load_env() {
  if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
  fi
}

require() {
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "error: '$cmd' is required but not installed" >&2; exit 1; }
  done
}

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
