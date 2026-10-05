# LLMLL backend

FastAPI + SQLAlchemy (SQLite) + Alembic. See `../ARCHITECTURE.md`.

```sh
uv sync                                   # install
uv run uvicorn --factory app.main:create_app --reload      # run (http://localhost:8000/api/health)
uv run alembic upgrade head               # apply migrations
uv run pytest -q                          # test
uv run ruff check . && uv run ruff format --check .   # lint
```

Configuration is read from `LLMLL_*` environment variables (`LLMLL_DATABASE_URL`,
`LLMLL_FRONTEND_DIST`, `LLMLL_LANGUAGETOOL_URL`) and `ANTHROPIC_API_KEY`.

LLM settings: `LLMLL_LLM_PROVIDER` (`anthropic` | `fake`; without an API key the fake client is
used), `LLMLL_LLM_REFUSAL_FALLBACK` (default true), `LLMLL_LLM_TASKS` (JSON per-task override of
model, effort and max_tokens, e.g. `{"grade_sentence": {"effort": "max"}}`).
