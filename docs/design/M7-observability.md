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

### 3.3 Event format (`GET /api/debug/llm/events`)
Content type `text/event-stream`. Frames are separated by a blank line:

```
id: 17
event: call_started
data: {"id": "b506…", "ts": "2026-10-08T06:21:27.229511+00:00", "task": "explain", …}

: ping
```

- `id:` is the broadcaster's sequence number (increasing within one server process, from 1). On
  reconnect `EventSource` sends it back as `Last-Event-ID` and only newer buffered events are
  sent; an id from a previous process (larger than the newest sequence) is ignored and the whole
  buffer is sent again. A client should de-duplicate on the call `id` anyway.
- `: ping` is a comment frame sent every 15 s while idle (ignored by `EventSource`).
- On connect the buffered events (up to 200, oldest first) are sent, then live ones.
- `data:` is one JSON object; timestamps are ISO 8601 with UTC offset. `id` below is the **call
  id** (a random hex string): it is the same in the `call_started` and the `call_finished` of one
  call and is how the client pairs them. An LLM call that is retried after invalid JSON is two
  calls (two ids).

`call_started` (sent right before the SDK request):

| Field | Type | |
|---|---|---|
| `id` | string | call id |
| `ts` | string | start time |
| `task` | string | `generate_exercise`, `grade_sentence`, `explain`, `simplify_text`, `gloss` |
| `provider`, `model` | string | e.g. `openrouter`, `vendor/model` |
| `prompt_version` | string | |
| `request` | object | the full request as logged (system prompt and user content inside `messages` for OpenAI-compatible, `system` / `messages` for Anthropic, `config.system_instruction` / `contents` for Gemini; the fake client logs the typed request) |
| `request_chars` | int or null | characters of system + user content |

`call_finished` (success or error; the full record):

| Field | Type | |
|---|---|---|
| `id`, `task`, `provider`, `model`, `prompt_version`, `request`, `request_chars` | | as above |
| `db_id` | int or null | `llm_calls.id` (null if the row could not be written) |
| `ts` | string | finish time (the `llm_calls.ts`) |
| `started_ts` | string or null | start time |
| `response` | any or null | the parsed response (JSON object), or the raw text / refusal object when it could not be parsed |
| `error` | string or null | e.g. `LLMUnavailable: …`; null on success |
| `stop_reason` | string or null | |
| `latency_ms` | int | total of the SDK call (retries included) |
| `attempts` | int or null | HTTP requests made; null when not traced (fake client) |
| `http_statuses` | (int or null)[] or null | status per attempt, e.g. `[429, 200]`; null for an attempt without a response |
| `retry_wait_ms` | int or null | first request to the start of the final attempt |
| `ttfb_ms` | int or null | final attempt: request sent to response headers; null if no response |
| `download_ms` | int or null | response headers to SDK return |
| `overhead_ms` | int or null | `latency_ms − (retry_wait + ttfb + download)`, floored at 0 |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` | int or null | |
| `upstream_provider` | string or null | OpenRouter only |

Throughput for display is `output_tokens / (ttfb_ms / 1000)`. `GET /api/debug/llm/calls` returns
the same fields (without `id`/`started_ts`; `id` there is the integer `llm_calls.id`).

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

Backend implementation (§1, §2, §3.1, §5 docs; the frontend pane §3.2 and its tests are not part of
it). SDKs verified: `openai` 3.24.0 and `anthropic` 1.11.0 `DefaultHttpxClient` forward
`event_hooks` to `httpx2.Client`; `google-genai` 2.28.0 uses classic `httpx` on its sync path.

- **google-genai uses `httpx`, not `httpx2`.** The Gemini client gets
  `HttpOptions(httpx_client=httpx.Client(event_hooks=..., follow_redirects=True))`; its
  `HttpOptions.timeout` still applies per request. The client built by us has httpx defaults
  (certifi, environment proxies) instead of the SDK's own SSL context helper.
- **The Anthropic adapter is not a `ProviderClient`**, so the trace is set in
  `AnthropicLLMClient._run` with the same helpers (`trace_call`, `stamp_timing`); behaviour is
  otherwise unchanged. `request_chars` there counts the text blocks of `system` and `messages`.
- **Timing fields can be null.** No HTTP request traced (fake client, mocked SDK in tests): all
  timing fields are null. Final attempt without a response (connection error/timeout):
  `ttfb_ms`, `download_ms` and `overhead_ms` are null (the time cannot be attributed) while
  `attempts`, `http_statuses` (null entries) and `retry_wait_ms` are kept. `ttfb_ms` includes
  TCP/TLS set-up, because the request hook fires before the connection is made.
- **Reasoning tokens semantics.** OpenAI-compatible `completion_tokens` already includes reasoning
  tokens (so `reasoning` can be close to `out`), Gemini `candidates_token_count` excludes thought
  tokens, Anthropic reports none. The displayed tok/s is `output_tokens / ttfb` as specified.
- **Broadcaster is per application, not a module singleton.** `create_app` makes
  `app.state.live = LiveBroadcaster(enabled=settings.debug)` and passes it to `SqlCallRecorder`.
  When debug is off `publish` is a no-op: nothing is buffered. The recorder gets a `started()`
  method that adapters call through `announce_start` before the SDK request (the recorder callable
  itself is only invoked when the call ends); `FakeLLMClient` announces right before recording its
  result. Calls recorded by the eval's `MemoryRecorder` are not published.
- **Call id.** The events carry a random per-call `id` (a `uid` field on `CallRecord`), because the
  database id does not exist when `call_started` is sent; `call_finished` adds `db_id`. SSE frames
  also carry an `id:` line and `Last-Event-ID` is honoured (addition to the spec, see §3.3).
- **Backpressure.** Per-subscriber queue of 100 events, oldest dropped for slow clients; ring
  buffer 200.
- **Order of checks on debug endpoints.** The access-token dependency runs before the debug check,
  so an unauthenticated request gets 401 and an authenticated one 404 when debug is off.
- **SSE test server.** Starlette's `TestClient` cannot stream an open response, so the streaming
  tests run the app under `uvicorn` in a thread on a free port and read with `httpx`.
- **Eval.** Failed calls (with their timings) are included in the latency block. LanguageTool time
  is measured once per case (`cases[].languagetool_ms`, `Progress.lt_ms`), only when LanguageTool
  is live. `--trace` writes each record as it arrives (an interrupted run keeps what it has).
  `eval-compare` adds the columns `lat p50`, `lat p90`, `ttfb p50`, `retries`, `reasoning`.
- **`llm-stats`** reads `llm_calls.ts` (the finish time) for `--since`; the pure aggregation and
  formatting are in `app/domain/llm_stats.py`, also used for the eval's latency block.
- **`check-llm --call --repeat N`** keeps the exit code 1 if any probe failed; the p50 is over the
  successful probes.
- **`LLMLL_LLM_TIMEOUT_S`** is a new setting (`llm_timeout_s`, default 120.0) replacing the
  hard-coded constant in `factory.py`.
