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
`LLMLL_FRONTEND_DIST`, `LLMLL_LANGUAGETOOL_URL`) and the provider API keys (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`).

LLM settings: `LLMLL_LLM_PROVIDER` (`anthropic` | `openai` | `google` | `openrouter` | `fake`;
without any API key the fake client is used), `LLMLL_LLM_MODEL` (required unless the provider is
`anthropic` or `fake`), `LLMLL_LLM_REFUSAL_FALLBACK` (Anthropic, default true), `LLMLL_LLM_TASKS`
(JSON per-task override of provider, model, effort, max_tokens, params and structured_output, e.g.
`{"grade_sentence": {"provider": "anthropic", "effort": "max"}}`). See "Choosing the LLM" in the
top-level `README.md`.
