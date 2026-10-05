#!/usr/bin/env bash
# Regenerate the OpenAPI schema and TypeScript types after changing backend API models.
source "$(dirname "$0")/_lib.sh"
require uv npm
(cd "$BACKEND" && uv run python -m app.cli export-openapi "$FRONTEND/src/api/openapi.json")
(cd "$FRONTEND" && npm run gen:api)
