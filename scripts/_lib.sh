# Shared helpers for the scripts in this folder. Source it, don't run it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
CURRICULUM="$ROOT/curriculum/de"

# Load $ROOT/.env into the environment, so API keys and settings reach the backend (which
# itself only reads environment variables). Variables already set in the calling shell win
# over .env (same rule as docker compose): `OPENROUTER_API_KEY=... scripts/x.sh` overrides
# the file, and an empty `KEY=` line in .env never wipes a key you exported.
load_env() {
  [[ -f "$ROOT/.env" ]] || return 0
  local before
  before="$(export -p)"
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
  # Re-apply what was exported before sourcing (export -p prints `declare -x NAME=...`).
  eval "${before//declare -x /export }"
}

require() {
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "error: '$cmd' is required but not installed" >&2; exit 1; }
  done
}

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
