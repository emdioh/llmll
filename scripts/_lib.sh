# Shared helpers for the scripts in this folder. Source it, don't run it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
CURRICULUM="$ROOT/curriculum/de"
CURRICULUM_PRIVATE="$ROOT/curriculum-private/de"  # optional, gitignored, personal use only

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

# Bring the local database to the schema this code expects (no-op when already current).
migrate_db() {
  (cd "$BACKEND" && uv run alembic upgrade head >/dev/null)
}

# Sets the global array CURRICULUM_ARGS to the import options: the public curriculum, plus the
# private one when it exists. Both must be imported in the same run (see AUTHORING §10).
# Use as: curriculum_args; cmd "${CURRICULUM_ARGS[@]}"
curriculum_args() {
  CURRICULUM_ARGS=(--path "$CURRICULUM")
  if [[ -d "$CURRICULUM_PRIVATE" ]]; then
    CURRICULUM_ARGS+=(--extra "$CURRICULUM_PRIVATE")
  fi
}

# Sets the global array COMPOSE to the compose command of a container engine (docker|podman).
# The engine is the argument, else $LLMLL_CONTAINER_ENGINE, else docker. Podman needs either
# the `podman compose` subcommand or the standalone podman-compose.
# Use as: compose_cmd [engine]; "${COMPOSE[@]}" exec -T app ...
compose_cmd() {
  local engine="${1:-${LLMLL_CONTAINER_ENGINE:-docker}}"
  case "$engine" in
    docker)
      require docker
      COMPOSE=(docker compose)
      ;;
    podman)
      if command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
        COMPOSE=(podman compose)
      elif command -v podman-compose >/dev/null 2>&1; then
        COMPOSE=(podman-compose)
      else
        echo "error: podman compose or podman-compose is required" >&2
        exit 1
      fi
      ;;
    *) echo "error: unknown container engine '$engine' (use docker or podman)" >&2; exit 1 ;;
  esac
}

# Prints the container engine whose compose project has the app service running: the engine in
# $LLMLL_CONTAINER_ENGINE if set, else docker, then podman (only installed ones are tried).
# Prints nothing when none is running. Probes with `exec`, because podman-compose has no
# `ps --status/--services`.
detect_engine() {
  local engine
  for engine in ${LLMLL_CONTAINER_ENGINE:-docker podman}; do
    command -v "$engine" >/dev/null 2>&1 || continue
    (compose_cmd "$engine" && cd "$ROOT" && "${COMPOSE[@]}" exec -T app true) >/dev/null 2>&1 || continue
    echo "$engine"
    return 0
  done
  return 0
}

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
