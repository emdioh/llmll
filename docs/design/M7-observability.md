# M7 — LLM observability: timing breakdown, stats, live debug pane

Design spec. Builds on the LLM layer (`docs/design/M2.md` §1, `M6-providers.md`) and the grader
evaluation (`M5.md`). Deviations go at the end of this file.

**Problem:** LLM calls feel slow and today only the total latency of each call is logged, so it is
impossible to tell whether the time goes to the network, the provider's queue, generation
(including hidden reasoning tokens), SDK retries with back-off, or our own pipeline.

**Usable result:** every LLM call records a timing breakdown and token detail; the eval and a stats
command summarize them; a debug pane in the web app shows every LLM exchange (full request and
response) live while using the app.

## 1. Per-call instrumentation (`app/llm/`)

### 1.1 HTTP tracing
- The SDK clients are built with our own HTTP client carrying request/response event hooks:
  `openai.DefaultHttpxClient(event_hooks=...)` and `anthropic.DefaultHttpxClient(event_hooks=...)`
  (both SDKs use `httpx2`); for google-genai pass `HttpOptions(httpx_client=...)` with a client of
  the HTTP library that SDK actually uses for sync calls (verify; if it cannot be hooked, record
  what is available and note it in Deviations). Timeouts and retry settings stay as today.
- A `ContextVar` holds the trace of the call in progress (set in `ProviderClient._attempt`); the
  hooks append one entry per HTTP attempt: `request_at`, `headers_at` (response hook = response
  headers received), `status`. Sync clients run hooks in the calling thread, so the context var
  is the right scope; verify with a test.
- Derived per call:
  - `attempts`: number of HTTP requests (1 = no retry);
  - `http_statuses`: list, e.g. `[429, 200]`;
  - `retry_wait_ms`: time from the first request to the start of the final attempt (failed
    attempts + SDK back-off);
  - `ttfb_ms`: final attempt, request sent → response headers (with non-streaming calls this is
    provider queue + full generation);
  - `download_ms`: response headers → SDK returned (body transfer + SDK parsing);
  - `overhead_ms`: everything else inside our call (prompt rendering, our JSON parsing,
    logging) = `latency_ms − (retry_wait_ms + ttfb_ms + download_ms)`, floored at 0.

### 1.2 Token and provider detail
- `reasoning_tokens`: OpenAI-compatible `usage.completion_tokens_details.reasoning_tokens`;
  Gemini `usage_metadata.thoughts_token_count`; Anthropic: not reported separately → null.
- `upstream_provider`: OpenRouter reports which upstream provider served the request (field
  `provider` in the response body, kept by the SDK as an extra attribute); null elsewhere.
- `request_chars`: total characters of system + user content sent (prompt size at a glance).
- Output throughput is derived for display: `output_tokens / ttfb_s`.

### 1.3 Storage
- `CallRecord` gains the fields above; `llm_calls` gets matching nullable columns (migration
  `0008`; `http_statuses` JSON).
- Settings: `LLMLL_LLM_TIMEOUT_S` (default 120) and the existing `LLMLL_LLM_MAX_RETRIES` are
  documented so retries can be set to 0 while diagnosing.

## 2. Reading the numbers

### 2.1 `eval-grader`
- Progress line: append a compact breakdown after the outcome, e.g.
  `llm 12.3s = wait 0.0s + ttfb 12.1s + dl 0.1s | 1 try | out 412 (reasoning 2140) | 34 tok/s | lt 0.2s`.
  LanguageTool time per case is measured in the runner.
- Summary: a "latency" block with p50 / p90 / max of total, ttfb, retry wait and LanguageTool
  time; total retries and the status codes that caused them; mean output and reasoning tokens;
  median output tok/s; upstream providers seen (OpenRouter).
- The saved report keeps per-run timing fields and the latency block; `eval-compare` adds rows
  for p50 / p90 total latency, p50 ttfb, retries and mean reasoning tokens.
- `--trace FILE` (optional) writes every call as one JSON line with the full request, response,
  timings and tokens (for offline inspection; not saved by default because of size).

### 2.2 `llm-stats` CLI (`scripts/llm-stats.sh`)
Reads `llm_calls` from the app database: `--last N` (default 200), `--task`, `--since 1h|1d`.
Per task × provider/model: calls, errors, p50 / p90 / max latency, p50 ttfb, retries, mean
in / out / reasoning tokens, median tok/s. Pure aggregation function in `app/domain/` or
`app/evals/`-style module, tested without a DB.

### 2.3 `check-llm --call --repeat N`
Prints the breakdown of each probe call and the p50; a tiny request isolates network and queue
time from generation time.

## 3. Live debug pane

### 3.1 Backend
- Enabled only when `LLMLL_DEBUG=true` (default false): it exposes full prompts and responses.
  `/api/health` reports `debug: true|false`. When disabled the debug endpoints return 404.
  Existing access control applies.
- In-process broadcaster (`app/llm/live.py`): the call recorder publishes `call_started`
  (id, task, provider, model, prompt version, full request, timestamp) and `call_finished`
  (the full record: response or error, timings, tokens). A ring buffer keeps the last 200 events.
  LLM calls run in worker threads (sync endpoints), subscribers are async SSE handlers: hand
  events across safely (e.g. `loop.call_soon_threadsafe` into per-subscriber `asyncio.Queue`s,
  bounded; drop the oldest for slow clients).
- `GET /api/debug/llm/events` — Server-Sent Events: first the buffered events, then live ones;
  heartbeat comment every 15 s. `GET /api/debug/llm/calls?limit=50` — recent calls from
  `llm_calls` (full rows) for history beyond the process lifetime.

### 3.2 Frontend
- When `debug` is true: a "Debug" entry in the navigation (`/debug`) and a small floating toggle
  available on every view that opens the same pane as a bottom drawer, so calls can be watched
  while doing a session or reading.
- Pane: live list, newest first, via `EventSource` (cookie auth works same-origin). Each row:
  time, task, provider/model (+ upstream provider), state (running with elapsed timer / ok /
  error), total, ttfb, retries (with statuses), tokens in/out/reasoning, tok/s. Expanding a row
  shows the system prompt, the user content, the raw response (pretty JSON) or the error, each
  with a copy button. Controls: pause/resume, clear, filter by task, a per-task p50/p90 strip over
  the buffered calls. Reconnects automatically.
- Mobile-first like the rest; the drawer must not cover the session's input permanently
  (collapsible, remembers its state in localStorage with try/catch).

## 4. Tests (must exist)
- Trace derivation (pure): single attempt; retry after 429 with back-off; failure; overhead floor.
- Hooks + context var with a real `httpx2` client against `httpx2.MockTransport` (or the SDK
  client with a mock transport): attempts, statuses and timestamps recorded on the right call.
- Reasoning tokens and upstream provider extraction for each adapter (mocked SDK objects).
- Migration `0008` and the autogenerate-diff test.
- Eval: progress line and latency block formatting; report fields; `eval-compare` rows.
- `llm-stats` aggregation; CLI on a temp DB.
- Debug: 404 when disabled; SSE endpoint streams buffered + live events published from another
  thread; buffer bound; health flag.
- Frontend: pane renders live events from a mocked EventSource, expand/copy, pause, filter;
  nav entry hidden when debug is false.

## 5. Docs
README (diagnosing slowness: what each number means, `LLMLL_DEBUG`, `llm-stats`), `.env.example`
(`LLMLL_DEBUG`, timeout/retries), ARCHITECTURE §7, `backend/evals/README.md`.

## Deviations
_(record any deviation from this spec here, with the reason)_
