#!/usr/bin/env bash
# Latency, retries and tokens of the LLM calls logged by the app (table in the app database),
# per task and provider/model. Read-only.
#   scripts/llm-stats.sh                     # the last 200 calls
#   scripts/llm-stats.sh --task grade_sentence --since 1h
#   scripts/llm-stats.sh --last 50
source "$(dirname "$0")/_lib.sh"
require uv
load_env
(cd "$BACKEND" && uv run python -m app.cli llm-stats "$@")
