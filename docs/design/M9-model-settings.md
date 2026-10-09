# M9 — Choosing the LLM models from Settings

Design spec. Builds on the provider layer (M6) and observability (M7). Deviations go at the end
of this file.

**Usable result:** on the Settings page the learner picks, per LLM task, the provider and the
model, starting with **grading** (`grade_sentence`) and **explanations** (`explain`). The change
takes effect immediately, without restarting the server. API keys stay in `.env`; nothing about
keys is ever sent to or accepted from the browser.

## 1. Precedence

From weakest to strongest:

1. built-in defaults (`app/llm/config.py`: Anthropic, `DEFAULT_MODEL`);
2. environment (`LLMLL_LLM_PROVIDER`, `LLMLL_LLM_MODEL`, `LLMLL_LLM_TASKS`);
3. **Settings overrides** (this milestone, stored in the database);
4. CLI flags of a single command (`eval-grader --provider/--model/--task-config`).

An override is per task and holds `provider`, `model` and optionally `structured_output`
(`native` | `json`; only for non-Anthropic providers). Clearing an override returns the task to
the environment configuration.

## 2. Storage

- New table `app_settings(key TEXT PRIMARY KEY, value JSON NOT NULL, updated_at DATETIME NOT
  NULL)`, Alembic migration `0010`, model in `app/store/models.py`. It holds app-level
  configuration (not learner state, so no event). This milestone uses one key, `llm_overrides`,
  value `{task: {provider, model, structured_output?}}`.
- Why not `learners.settings`: the LLM client is shared by the whole server, the choice is an
  operator setting, and a future multi-learner setup must not let one learner switch everyone's
  model.

## 3. Backend

### 3.1 Merge (`app/llm/overrides.py`, pure)
- `apply_overrides(settings: Settings, overrides: dict) -> Settings`: returns a copy of the
  settings whose `llm_tasks` has, for each overridden task, the environment entry with
  `provider` and `model` replaced (and `structured_output` when given). If the override's
  provider differs from the provider the environment resolves for that task (task provider, else
  `llm_provider`), the provider-specific fields of the environment entry (`effort`, `params`,
  `structured_output`) are dropped, so that e.g. an Anthropic `effort` doesn't leak into an
  OpenRouter route. `max_tokens` is kept.
- `OverrideIn` Pydantic model: `provider` in the real providers (not `fake`), `model` stripped,
  1–200 chars, `structured_output` optional; `structured_output` with `provider='anthropic'` is
  rejected. Unknown task names are rejected.
- `load_overrides(session) -> dict` / `save_overrides(session, overrides, now)` read and write
  the `app_settings` row (invalid stored entries are skipped with a warning, never crash).

### 3.2 Building and swapping the client
- `create_app` stores a builder in `app.state.llm_builder: Callable[[dict], LLMClient] | None`
  (None when an `llm` is injected, as in most tests). At startup it loads the stored overrides
  (skipped when the schema is out of date) and builds the client from `apply_overrides(...)`.
  If that build fails (e.g. the key of an overridden provider was removed from `.env`), it logs
  an error, builds from the environment alone, and remembers the message in
  `app.state.llm_override_error`.
- Applying a change: validate the full merged configuration by building the new client first;
  on `LLMConfigError` respond 422 with its message and change nothing. On success save the
  overrides, then assign `app.state.llm` (a single attribute assignment; requests already in
  flight finish on the old client). Clear `llm_override_error`.
- With the fake fallback (no API key at all) overrides cannot apply: GET reports `fake: true`,
  PUT answers 409.
- With an injected client (`llm_builder is None`) PUT still validates and stores but cannot
  swap; GET reports `live_switch: false`. (Tests that need the swap build the app without an
  injected client, using dummy keys: building SDK clients makes no network call.)

### 3.3 Endpoints (under the existing auth dependency)
- `GET /api/settings/llm` → `LLMSettingsOut`:
  ```
  { fake: bool, live_switch: bool, override_error: str | null,
    available_providers: [provider with a key set, in PROVIDERS order],
    tasks: [ { task, provider, model, structured_output,            # effective route now
               source: "override" | "config",
               config_provider, config_model,                     # what .env alone gives
               override: {provider, model, structured_output} | null } ] }
  ```
  Tasks in this order: `grade_sentence`, `explain`, `generate_exercise`, `simplify_text`,
  `gloss`. If the environment configuration itself is invalid, `config_*` are null.
- `PUT /api/settings/llm` with `{ tasks: { <task>: OverrideIn | null } }`: partial update (tasks
  not listed keep their override; `null` clears). The provider must be in
  `available_providers` (422 otherwise). Returns `LLMSettingsOut`.
- `operation_id`s `getLlmSettings`, `updateLlmSettings`; regenerate `frontend/src/api/openapi.json`
  and `schema.d.ts` (`scripts/gen-api.sh`).
- `/api/health` already reports `llm_tasks` from the live client, so it reflects the switch.

### 3.4 CLI
- `check-llm` and `eval-grader` apply the stored overrides (when the database is reachable and
  up to date; otherwise they print one note and continue with the environment), so they test the
  models the app actually uses. `check-llm` marks overridden routes with `(Settings)`.
- `eval-grader`: `--task-config`, `--provider` and `--model` are applied on top, as today. The
  saved run metadata already records the grader route.

## 4. Frontend
- Settings page (English labels, like the rest of the page), new section **"LLM models"**: one
  row per task; **Grading** and **Explanations** shown first, the other three (Exercise
  generation, Text simplification, Glosses) under a collapsed "Other tasks". Each row: provider
  `<select>` (only `available_providers`), model text input (placeholder = `config_model`), for
  non-Anthropic an "Output" select (native / json, default native), a "Reset" button when an
  override is set, and a muted line "from .env: provider/model" when overridden. One "Save" for
  the section; show the server's 422 message inline.
- `fake: true` → the section shows a short notice (no API key configured, the simulated LLM is
  in use) and no inputs. `override_error` → warning banner with the message.
  `live_switch: false` → note that a restart is needed.
- Changing provider clears the model field (model ids are provider-specific).
- Mobile-first, works at 360 px.

## 5. Tests (must exist)
- `apply_overrides`: replace provider/model; same provider keeps `effort`/`params`; different
  provider drops them; `structured_output` passes; env tasks without override unchanged.
- Validation: unknown task, `fake` provider, empty model, `structured_output` with anthropic,
  OpenRouter-style id for anthropic (existing `_check_model_matches_provider`), provider without
  key → 422, nothing stored.
- Endpoints on an app built with dummy keys for two providers and no injected client: GET shape
  and order; PUT switches `/api/health` routes immediately and the stored row; `null` clears;
  partial PUT keeps other overrides; fake fallback → 409 and `fake: true`.
- Startup: stored overrides applied on a new app; a stored override whose key is gone →
  environment config, `override_error` set, no crash.
- Migration test stays green (model and migration match).
- CLI: `check-llm` routing output shows a stored override.
- Frontend: section renders rows from mocked GET, saves a changed model with the right PUT body,
  reset sends `null`, shows 422 message, fake notice.

## Deviations
- `validate_routes` and `available_providers` were added to `app/llm/factory.py` (the validation
  of an injected-client app and the key check reuse them); no behaviour change there.
- `fake` in `GET` is "no API key at all" (settings-based), so it also holds for an injected client
  on a keyless configuration.
- `eval-grader` applies only the stored `grade_sentence` override (the eval only grades, and
  other tasks' overrides would require their providers' keys). Without `--provider`/`--model`
  it also routes the other tasks to the grading override's provider, as `--provider` does, so
  only that provider's key is needed. `check-llm` applies all of them.
- Reset in the UI sends its `null` immediately (its own PUT) rather than waiting for "Save";
  the section's save button is labelled "Save models" to tell it from the page's own "Save".
- The migration directory is `backend/migrations/versions/` (0010), not `alembic/versions`.
