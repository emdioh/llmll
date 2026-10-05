#!/usr/bin/env bash
# Show which provider, model and key each LLM task uses (keys are never printed), after
# loading .env exactly like the other scripts. Add --call to make one tiny real request.
#   scripts/check-llm.sh            # configuration only, free
#   scripts/check-llm.sh --call     # plus a test call (costs a fraction of a cent)
source "$(dirname "$0")/_lib.sh"
require uv
load_env
(cd "$BACKEND" && uv run python -m app.cli check-llm "$@")
