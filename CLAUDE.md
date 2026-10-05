# CLAUDE.md

Guidance for AI assistants working in this repo.

- Read `REQUIREMENTS.md` and `ARCHITECTURE.md` before non-trivial changes; keep them in sync
  with the code. All documentation and code comments are in English.
- Architecture rules that must not be broken:
  - `learning_events` is append-only; item state (`item_memory`) is a projection rebuilt
    from events. Never update memory/mastery state without an event.
  - `app/domain/` contains pure functions only: no database, network or LLM access.
  - The LLM is reached only through the typed `LLMClient` interface in `app/llm/`; prompts
    are versioned files and every call is logged.
  - Curriculum content lives in `curriculum/` YAML, never in code. Item ids are stable and
    never reused.
- Before committing, run the same checks as CI (`scripts/check.sh` does all of this):
  - backend: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
  - frontend: `cd frontend && npm run lint && npm run typecheck && npm run format:check && npm test && npm run build`
- Schema changes go through Alembic migrations; `tests/test_migrations.py` checks that
  models and migrations match.
