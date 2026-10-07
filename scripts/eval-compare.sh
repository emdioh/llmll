#!/usr/bin/env bash
# Compare saved grader evaluation runs (backend/evals/results).
#   scripts/eval-compare.sh               # table of the last 20 runs
#   scripts/eval-compare.sh --last 5      # the last 5
#   scripts/eval-compare.sh base v2       # two runs side by side + per-case differences
# Runs are picked by file path or any unique part of the file name.
source "$(dirname "$0")/_lib.sh"
require uv
(cd "$BACKEND" && uv run python -m app.cli eval-compare "$@")
