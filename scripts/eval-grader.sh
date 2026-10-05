#!/usr/bin/env bash
# Measure the grader on the annotated cases (costs API credits: ~70 calls per run).
# Loads .env, starts LanguageTool if Docker is available, writes a timestamped JSON report.
#
#   scripts/eval-grader.sh                                   # configured provider/model
#   scripts/eval-grader.sh --provider openrouter --model '<vendor>/<model>'
#   scripts/eval-grader.sh --provider anthropic --model claude-sonnet-5-5 --repeat 2
#
# Any extra arguments are passed to `app.cli eval-grader` (see --help).
source "$(dirname "$0")/_lib.sh"
require uv
load_env

extra=()
if [[ " $* " != *" --no-languagetool "* ]]; then
  if command -v docker >/dev/null 2>&1; then
    "$(dirname "$0")/languagetool.sh" || { echo "continuing without LanguageTool"; extra+=(--no-languagetool); }
  else
    echo "docker not found: running without LanguageTool"; extra+=(--no-languagetool)
  fi
fi

mkdir -p "$ROOT/evals-reports"
out="$ROOT/evals-reports/grader-$(date +%Y%m%d-%H%M%S).json"
(cd "$BACKEND" && uv run python -m app.cli eval-grader --out "$out" ${extra[@]+"${extra[@]}"} "$@")
echo "report: $out"
