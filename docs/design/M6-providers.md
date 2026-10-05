# M6 — Multiple LLM providers (Anthropic, OpenAI, Google, OpenRouter)

Design spec. Builds on the LLM layer of M2/M3 (`docs/design/M2.md` §1, `M3.md` §3).
Deviations go at the end of this file.

**Usable result:** every LLM task (`generate_exercise`, `grade_sentence`, `explain`,
`simplify_text`, `gloss`) can run on Anthropic, OpenAI, Google Gemini or any model on
OpenRouter, chosen globally or per task, and `eval-grader` can compare providers on the same
cases.

## 1. Decisions
- **Official SDKs, no abstraction library.** `openai` (also used for OpenRouter, which exposes
  an OpenAI-compatible API at `https://openrouter.ai/api/v1`) and `google-genai` for Gemini.
  A multi-provider library (e.g. LiteLLM) was rejected: heavy and fast-moving, against the
  "mainstream and maintainable" criterion (R§11). Each adapter is small and isolated.
- **The `LLMClient` protocol does not change.** Services keep calling typed tasks; adapters
  live in `app/llm/` next to `AnthropicLLMClient` and `FakeLLMClient`.
- **No default model ids for the new providers.** Model names change often; the configuration
  must name the model explicitly. Startup fails with a clear message when a task resolves to a
  non-Anthropic provider without a model. Anthropic keeps its default (`claude-opus-5-5`).
- **Prompts are shared.** The same versioned prompt files are used for every provider. The
  stable part goes first, so providers with automatic prefix caching (OpenAI, Gemini) benefit
  too; only Anthropic gets explicit `cache_control`.

## 2. Configuration
- `LLMLL_LLM_PROVIDER`: default provider — `anthropic` (default), `openai`, `google`,
  `openrouter`, `fake`.
- `LLMLL_LLM_MODEL`: default model for the default provider (required unless `anthropic` or
  `fake`).
- `LLMLL_LLM_TASKS` (JSON, existing) gains optional per-task `provider`, `model` and `params`:
  ```json
  {"grade_sentence": {"provider": "anthropic", "effort": "high"},
   "gloss": {"provider": "openrouter", "model": "<vendor>/<model>"},
   "simplify_text": {"provider": "google", "model": "<gemini model id>"}}
  ```
  Resolution per task: task setting → global default → built-in default (Anthropic only).
- `params`: provider-specific request parameters passed through unchanged (e.g.
  `{"reasoning_effort": "high"}` for OpenAI reasoning models, a thinking config for Gemini).
  `effort` stays Anthropic-only; it is ignored with a one-time warning for other providers.
- `structured_output`: per task, `native` (default) or `json`. `native` uses the provider's
  schema-constrained output; `json` asks for JSON in the prompt/response format and validates
  with Pydantic, retrying once with the validation error appended. Needed for OpenRouter
  models without schema support.
- API keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`),
  `OPENROUTER_API_KEY`. Optional `LLMLL_OPENAI_BASE_URL` (OpenAI-compatible endpoints) and
  `LLMLL_OPENROUTER_APP_NAME` / `LLMLL_OPENROUTER_SITE_URL` (sent as `X-Title` /
  `HTTP-Referer`).
- Fallback to `FakeLLMClient` stays only for the whole app when **no** provider key at all is
  configured; a task configured for a provider whose key is missing is a startup error.

## 3. Adapters (`app/llm/`)
- `openai_client.py` — `OpenAICompatibleLLMClient(client, provider_name)`, used for `openai`
  and `openrouter`:
  - messages: `system` = system prompt; `user` = stable block followed by variable block;
  - native structured output via the SDK's parse helper with the Pydantic model as response
    format; refusal (`message.refusal`) → `LLMRefusal`; truncation (`finish_reason == "length"`)
    → `LLMError`;
  - usage: prompt/completion tokens, cached prompt tokens when reported.
- `google_client.py` — `GeminiLLMClient(client)` with `google-genai`:
  - `system_instruction` = system prompt; contents = stable + variable blocks;
  - native structured output via JSON mime type + response schema from the Pydantic model;
  - blocked prompt or safety finish reason → `LLMRefusal`; max-tokens finish → `LLMError`;
  - usage: prompt, candidates, cached-content token counts.
- Shared: error mapping (rate limit / connection / 5xx → `LLMUnavailable`, other API errors →
  `LLMError`), `llm_calls` logging, the `json` structured-output fallback.
- **Schema compatibility:** before relying on native mode, check that each response model's
  schema is accepted by each SDK's converter (OpenAI strict mode requires every property to be
  required and no additional properties; Gemini supports a subset of OpenAPI). Adjust the
  response models (not the prompts) where needed, keeping Anthropic behaviour unchanged, or
  document per model that `json` mode is required.
- `factory.py`: builds one client per provider actually used and a `RoutingLLMClient` that
  dispatches each task to its configured provider/model. Single-provider setups behave exactly
  as before.

## 4. Logging and health
- `llm_calls` gains `provider` (migration `0007`, existing rows backfilled as `anthropic` or
  `fake`).
- `/api/health` `llm` becomes the default provider name (`anthropic | openai | google |
  openrouter | fake`), plus `llm_tasks: {task: "provider/model"}`. The frontend banner logic
  (shown only for `fake`) keeps working.

## 5. Evaluation
`eval-grader` gains `--provider` and `--model` (override for `grade_sentence`) and records them
in the report, so providers can be compared on the same cases (`backend/evals/README.md`).

## 6. Tests (must exist)
- Each adapter with a mocked SDK client: request shape (system/stable/variable placement,
  model, params passthrough, structured output argument), parsed output, refusal, truncation,
  error mapping, usage logging with `provider`.
- `json` structured-output mode: valid JSON, invalid then valid on retry, invalid twice →
  `LLMError`.
- Schema compatibility test: every task response model converts with the OpenAI strict-schema
  helper and the google-genai schema conversion without errors (no network).
- Factory/routing: per-task resolution, missing model → startup error, missing key for a
  configured provider → startup error, no keys at all → fake.
- `eval-grader --provider ... --model ...` with mocked clients.
- No network calls in tests.

## 7. Docs
README (configuration examples for each provider), `.env.example`, `ARCHITECTURE.md` §7,
`REQUIREMENTS.md` §11 table (LLM row).

## Deviations
_(record any deviation from this spec here, with the reason)_
