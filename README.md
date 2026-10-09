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
| `curriculum/` | Curriculum content as YAML — how to write it: [`curriculum/AUTHORING.md`](curriculum/AUTHORING.md) |

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

## Diagnosing slow or failing LLM calls

Every call is logged in the `llm_calls` table with a timing breakdown (`eval-grader`,
`check-llm --call` and the debug pane show the same numbers):

| Number | Meaning |
|---|---|
| `total` (`latency_ms`) | The whole SDK call, retries included |
| `wait` (`retry_wait_ms`) | Time before the final HTTP attempt: failed attempts (e.g. a 429) plus the SDK's back-off. High = rate limiting or provider errors |
| `ttfb` | Final attempt: request sent to response headers. Calls are not streamed, so this is connection set-up + the provider's queue + the whole generation, including hidden reasoning tokens. High = slow model or a busy provider |
| `dl` (`download_ms`) | Response headers to the SDK returning: body transfer and parsing (normally tiny) |
| `overhead_ms` | The rest of our call (prompt rendering, JSON parsing); normally ~0 |
| `tries` / statuses | HTTP requests made, e.g. `2 tries (429,200)` |
| `out` / `reasoning` | Output tokens and the hidden reasoning tokens (OpenAI-compatible: included in `out`; Gemini: counted separately; Anthropic: not reported) |
| `tok/s` | `out / ttfb`, a rough throughput |
| `upstream` | OpenRouter only: which upstream provider served the call |
| `lt` | LanguageTool time of the case (`eval-grader` only) |

Rule of thumb: a large `wait` points at rate limits, a large `ttfb` at the model (try a smaller
or non-reasoning model, lower `reasoning_effort`), a large `overhead` at our code.

- **Settings.** `LLMLL_LLM_TIMEOUT_S` (default 120) is the HTTP timeout of one request and
  `LLMLL_LLM_MAX_RETRIES` (default 2) the SDK's retries with back-off: set the retries to `0`
  while diagnosing, so a failing call is not retried silently.
- **`scripts/llm-stats.sh [--last N] [--task T] [--since 1h|1d]`** summarizes the log per task and
  provider/model: calls, errors, p50 / p90 / max latency, p50 ttfb, retries, mean tokens and
  median tok/s.
- **`scripts/check-llm.sh --call --repeat 3`** makes tiny test requests and prints the breakdown of
  each and the p50: a small request separates network and queue time from generation time.
- **`LLMLL_DEBUG=true`** turns on the live debug endpoints (`GET /api/debug/llm/events`, a
  Server-Sent Events stream, and `GET /api/debug/llm/calls`) that show every LLM exchange with the
  **full prompts and responses**; they answer 404 otherwise, and the normal access token applies.
  `/api/health` reports `debug`. Keep it off on a shared server. The fake LLM publishes events
  too, so it can be tried without an API key.

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

### From GitHub with Railway (works from a phone browser)

The repo has a `railway.json` (Dockerfile build, health check on `/api/health`), and the container
listens on the `PORT` the platform provides. Sizing: the app needs ~150 MB idle and ~500 MB once
the reading feature has loaded spaCy, so give it **at least 1 GB of RAM**; LanguageTool is
optional (grading works without it) and needs ~1 GB more.

1. railway.com → sign in with GitHub → **New Project → Deploy from GitHub repo** → pick this repo.
   In the service's **Settings → Source**, choose the branch to deploy.
2. **Variables** — add:
   - the LLM settings and key, e.g. `LLMLL_LLM_PROVIDER=openrouter`, `LLMLL_LLM_MODEL=<id>`,
     `OPENROUTER_API_KEY=…` (or `ANTHROPIC_API_KEY=…`);
   - `LLMLL_ACCESS_TOKEN` = a long random value (a password manager can generate one) —
     **required**, otherwise anyone with the URL can use your API credits;
   - `LLMLL_COOKIE_SECURE=true` (Railway serves HTTPS).
3. **Volume**: right-click/long-press the service → **Attach volume**, mount path **`/data`**
   (the SQLite database lives there; without a volume every deploy erases your progress).
4. **Settings → Networking → Generate domain**. Open it, log in with the access token, and
   "Add to Home screen" to install the app.
5. Optional LanguageTool: **New → Docker image** `erikvl87/languagetool`, then on the app set
   `LLMLL_LANGUAGETOOL_URL=http://<languagetool service private domain>:8010`.

Every push to the chosen branch redeploys; migrations and the curriculum import run at start.
Your data is the single file `/data/llmll.db` on the volume: enable the platform's volume
backups if available, and keep an occasional copy of that file.

## Scripts

Common tasks live in [`scripts/`](scripts/) (Bash). They find the repo root and load `.env`
into the environment of the commands they run — that's where API keys and LLM settings come
from; variables already set in your shell take precedence over `.env`.

| Script | What it does |
|---|---|
| `scripts/setup.sh` | First-time setup: install dependencies, migrate the database, import the curriculum |
| `scripts/dev.sh` | Backend (:8000, auto-reload) + frontend dev server (:5173); Ctrl+C stops both |
| `scripts/check.sh [backend\|frontend]` | The same checks as CI, plus the API-schema sync check |
| `scripts/gen-api.sh` | Regenerate the OpenAPI schema and TypeScript types after API changes |
| `scripts/import-curriculum.sh` | Validate and import `curriculum/de` after editing the YAML |
| `scripts/replay.sh` | Rebuild memory projections from the event log |
| `scripts/languagetool.sh [stop]` | Start/stop a local LanguageTool on :8010 (Docker) |
| `scripts/check-llm.sh [--call]` | Which provider/model/key each task uses (keys not printed); `--call` makes one test request |
| `scripts/eval-grader.sh [args]` | Grader evaluation with LanguageTool; every run is saved to `backend/evals/results/` (`--label` to tag it; costs API credits) |
| `scripts/llm-stats.sh [--last N] [--task T] [--since 1h]` | Latency, retries and tokens per task and model, from the `llm_calls` log |
| `scripts/eval-compare.sh [runs]` | Compare saved runs: table of recent runs, or two runs side by side with per-case differences |
| `scripts/backup.sh [docker\|local]` | Consistent SQLite backup into `backups/` |
| `source scripts/env.fish [--force]` | fish shell: load `.env` into the current shell (bash/zsh: `set -a; . ./.env; set +a`) |
| `scripts/up.sh [logs\|down]` | Full app with Docker Compose on :8000 |

## Development

```sh
scripts/setup.sh   # once
scripts/dev.sh     # backend + frontend; open http://localhost:5173
scripts/check.sh   # before committing
```

The Vite dev server proxies `/api` to the backend. To run the parts by hand:
`cd backend && uv run uvicorn --factory app.main:create_app --reload` and
`cd frontend && npm run dev`.

CI (`.github/workflows/ci.yml`) runs lint, format check, type check and tests for both
parts, then builds the Docker image.
