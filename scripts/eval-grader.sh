#!/usr/bin/env bash
# Measure the grader on the annotated cases (costs API credits: ~70 calls per run).
# Loads .env, starts LanguageTool if Docker is available; every run is saved to
# backend/evals/results/ (compare with scripts/eval-compare.sh). Use --label "note" to tag it.
#
#   scripts/eval-grader.sh                                   # configured provider/model
#   scripts/eval-grader.sh --provider openrouter --model '<vendor>/<model>'
#   scripts/eval-grader.sh --provider anthropic --model claude-sonnet-5-5 --label baseline
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

(cd "$BACKEND" && exec uv run python -m app.cli eval-grader ${extra[@]+"${extra[@]}"} "$@")
