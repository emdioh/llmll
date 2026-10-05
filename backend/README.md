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
