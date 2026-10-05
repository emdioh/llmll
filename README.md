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
