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
cp .env.example .env      # add the API key of your LLM provider (e.g. ANTHROPIC_API_KEY)
docker compose up --build
```

The app is served on <http://localhost:8000> (API under `/api`, OpenAPI docs at `/docs`).
LanguageTool runs as a separate container. Data lives in the `llmll-data` volume
(SQLite); back it up by copying `/data/llmll.db`.

## Choosing the LLM

Every LLM task (`generate_exercise`, `grade_sentence`, `explain`, `simplify_text`, `gloss`) can
run on Anthropic, OpenAI, Google Gemini or any model on OpenRouter, globally or per task. Put the
settings in `.env`. Anthropic is the default and has a default model; for every other provider
you must name the model (model ids change often, so none is built in).

```sh
# Anthropic (default model claude-opus-5-5; LLMLL_LLM_MODEL overrides it)
ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
LLMLL_LLM_PROVIDER=openai
LLMLL_LLM_MODEL=<model id>
OPENAI_API_KEY=sk-...
# LLMLL_OPENAI_BASE_URL=https://my-gateway.example/v1   # any OpenAI-compatible endpoint

# Google Gemini
LLMLL_LLM_PROVIDER=google
LLMLL_LLM_MODEL=<gemini model id>
GEMINI_API_KEY=...                                       # or GOOGLE_API_KEY

# OpenRouter (any model it offers; copy the id from https://openrouter.ai/models, <vendor>/<model>).
# No base URL to set: the openrouter provider always uses https://openrouter.ai/api/v1.
# (LLMLL_OPENAI_BASE_URL is only for the openai provider pointing at another endpoint.)
LLMLL_LLM_PROVIDER=openrouter
LLMLL_LLM_MODEL=<vendor>/<model>
OPENROUTER_API_KEY=sk-or-...
# LLMLL_OPENROUTER_APP_NAME=LLMLL                        # sent as X-Title
# LLMLL_OPENROUTER_SITE_URL=https://llmll.example.com    # sent as HTTP-Referer
```

Mixing providers per task uses `LLMLL_LLM_TASKS` (JSON). Resolution per task is: task setting,
then `LLMLL_LLM_PROVIDER` / `LLMLL_LLM_MODEL`, then the built-in default (Anthropic only). The
key of every provider in use must be set; the app refuses to start otherwise, and also when a
task resolves to a provider without a model. With no key at all it runs on a fake LLM.

```sh
LLMLL_LLM_TASKS='{"grade_sentence": {"provider": "anthropic", "effort": "high"}, "gloss": {"provider": "openrouter", "model": "<vendor>/<model>"}, "simplify_text": {"provider": "google", "model": "<gemini model id>"}}'
```

Keep the JSON on one line and in single quotes: that works both in `.env` for docker compose
and when loading `.env` into a shell. `.env.example` has a commented block per provider.

Per-task fields: `provider`, `model`, `max_tokens`, `effort` (Anthropic only, ignored with a
warning elsewhere), `params` (provider-specific request parameters passed through unchanged, e.g.
`{"reasoning_effort": "high"}` for OpenAI reasoning models or `{"thinking_config":
{"thinking_budget": 1024}}` for Gemini) and `structured_output`: `native` (default,
schema-constrained output) or `json` (JSON requested in the prompt, validated, one retry; for
OpenRouter models without schema support; not available for Anthropic). `GET /api/health`
reports the default provider (`llm`) and the routing (`llm_tasks`, `provider/model` per task).
To compare providers on the grader, see [`backend/evals/README.md`](backend/evals/README.md).

## Deploying on a server

The app has no user accounts; on a public server a single shared access token protects the API
(and your Anthropic credits). Static frontend files stay public, every `/api/*` route except
`GET /api/health` and `/api/auth/*` requires the token.

1. Generate a long random token and put it in `.env`:

   ```sh
   echo "LLMLL_ACCESS_TOKEN=$(openssl rand -hex 32)" >> .env
   echo "LLMLL_COOKIE_SECURE=true" >> .env
   ```

   The login screen asks for the token once and sets an HttpOnly, SameSite=Strict session cookie
   (90 days). The cookie holds an HMAC derived from the token, so changing the token logs
   everybody out. Scripts can send `Authorization: Bearer <token>` instead. Failed logins are
   throttled in memory (5 failures per 15 minutes, then HTTP 429 for 15 minutes).
2. Serve it over HTTPS through a reverse proxy; never expose port 8000 directly. With
   [Caddy](https://caddyserver.com/), which obtains certificates automatically, a minimal
   `Caddyfile` is:

   ```
   llmll.example.com {
       reverse_proxy app:8000
   }
   ```

   Add Caddy to `docker-compose.yml` (commented out by default; also remove the `ports` mapping of
   `app` so only the proxy is reachable):

   ```yaml
   #  caddy:
   #    image: caddy:2
   #    ports:
   #      - "80:80"
   #      - "443:443"
   #    volumes:
   #      - ./Caddyfile:/etc/caddy/Caddyfile:ro
   #      - caddy-data:/data
   #    depends_on:
   #      - app
   #    restart: unless-stopped
   # and under `volumes:` add `caddy-data:`
   ```

   Note that the login throttle keys on the client address seen by the app, which behind a proxy
   is the proxy's: failed logins from anyone count together.
3. Back up the `llmll-data` volume regularly (it holds the SQLite database, `/data/llmll.db`),
   e.g. by copying `/data/llmll.db` out of the volume while the app is stopped
   (`docker compose stop app && docker compose cp app:/data/llmll.db ./backup.db`).
4. Check `GET /api/health`: `"auth": "enabled"` confirms that the token is active.

## Development

Run backend and frontend separately; the Vite dev server proxies `/api` to the backend.

```sh
cd backend && uv sync && uv run alembic upgrade head && uv run uvicorn --factory app.main:create_app --reload
cd frontend && npm ci && npm run dev
```

CI (`.github/workflows/ci.yml`) runs lint, format check, type check and tests for both
parts, then builds the Docker image.
