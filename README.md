# LLMLL

A language-learning app that combines a structured curriculum, spaced repetition (FSRS)
over everything the learner studies, and an LLM that generates exercises and texts,
grades free-form sentences and explains grammar. First target: German for Italian/English
speakers.

- [`REQUIREMENTS.md`](REQUIREMENTS.md): what the app must do, and why.
- [`ARCHITECTURE.md`](ARCHITECTURE.md): how it is built, data model, milestones.

## Layout

| Path | Content |
|---|---|
| `backend/` | Python + FastAPI, SQLAlchemy, Alembic — see [`backend/README.md`](backend/README.md) |
| `frontend/` | React + TypeScript (Vite), PWA — see [`frontend/README.md`](frontend/README.md) |
| `curriculum/` | Curriculum content as YAML (from milestone M1) |

## Running everything with Docker

```sh
cp .env.example .env      # add your ANTHROPIC_API_KEY
docker compose up --build
```

The app is served on <http://localhost:8000> (API under `/api`, OpenAPI docs at `/docs`).
LanguageTool runs as a separate container. Data lives in the `llmll-data` volume
(SQLite); back it up by copying `/data/llmll.db`.

## Development

Run backend and frontend separately; the Vite dev server proxies `/api` to the backend.

```sh
cd backend && uv sync && uv run alembic upgrade head && uv run uvicorn app.main:app --reload
cd frontend && npm ci && npm run dev
```

CI (`.github/workflows/ci.yml`) runs lint, format check, type check and tests for both
parts, then builds the Docker image.
