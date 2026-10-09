# LLMLL (LLM Language Learning) — Architecture

> Living document, companion to [`REQUIREMENTS.md`](REQUIREMENTS.md): references of the
> form `R§n` point to sections of the requirements.
> Status: v1.0 (October 2026) — describes the system as built through milestone M5.
> Detailed per-milestone designs, including every deviation decided during implementation,
> are in [`docs/design/`](docs/design/): `M1.md` … `M5.md`. Where this document and a
> milestone's Deviations section disagree, the Deviations section is authoritative.

## 1. Architectural principles

1. **Event history is the source of truth.** Every learning outcome is an append-only
   event. The state of each item (FSRS memory, mastery) is a **projection** that can be
   recomputed from the events. This makes it possible to revert contests (R§8), optimise
   FSRS and change the mastery model without losing data.
2. **Pure domain, I/O at the edges.** Scheduling, FSRS grading, mastery and item selection
   are pure functions, with no database or LLM, testable in isolation.
3. **The LLM is a component, not the brain.** The app decides *what* to practise; the LLM
   generates text and grades answers within structured schemas. Every call is logged.
4. **Content is data.** The curriculum is versioned YAML; prompts are versioned files.
5. **Mainstream stack** (R§11): Python/FastAPI, SQLite/SQLAlchemy, React/TypeScript.

## 2. Overview

```
┌──────────────────────────── Frontend (React + TS, PWA) ────────────────────────────┐
│  Review session │ Reading │ Corpus │ Grammar │ Settings                            │
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                        │ REST/JSON
┌───────────────────────────── Backend (FastAPI) ────────────────────────────────────┐
│  api/            HTTP endpoints, validation (Pydantic)                             │
│  services/       use-case orchestration                                            │
│    session_builder · exercise_service · grading_service · reading_service         │
│    contest_service · placement_service                                             │
│  domain/         pure functions                                                    │
│    scheduling (FSRS) · mastery · grade_mapping · new_item_budget · word_classifier │
│  llm/            LLMClient interface + typed tasks + versioned prompts             │
│  nlp/            lemmatization (spaCy) · compounds · frequencies · LanguageTool    │
│  store/          SQLAlchemy models · event log · projections                       │
└──────────┬──────────────────────┬──────────────────────┬───────────────────────────┘
           │                      │                      │
     SQLite (file)        LanguageTool (container)   Claude API
           ▲
   curriculum/*.yaml ──(import script)
```

## 3. Repository layout

```
llmll/
├── REQUIREMENTS.md  ARCHITECTURE.md  CLAUDE.md
├── docs/design/M1.md … M5.md   # milestone designs + deviations
├── docker-compose.yml          # backend + languagetool (+ frontend as a static build)
├── curriculum/de/              # content, versioned in git
│   ├── lexicon/a1.yaml a2.yaml b1.yaml
│   ├── grammar/*.yaml          # one file per grammar point, with reference text
│   └── constructions.yaml
├── curriculum-private/de/      # gitignored, personal use only, never committed (AUTHORING §10)
├── backend/
│   ├── pyproject.toml
│   ├── app/{api,services,domain,llm,nlp,store,curriculum,evals}/
│   ├── app/llm/prompts/        # versioned templates (e.g. grade_sentence.v3.md)
│   ├── migrations/             # Alembic
│   ├── app/cli.py              # import-curriculum, replay, export-openapi, eval-grader,
│   │                           # export-contests, optimize-fsrs
│   ├── evals/grader/cases/     # annotated grader evaluation cases
│   └── tests/
└── frontend/
    ├── package.json
    └── src/
```

## 4. Data model

### 4.1 Content (imported from the curriculum)

**`items`**: all knowledge items (R§4.1).

| Field | Notes |
|---|---|
| `id` | stable slug, never reused: `lex:tisch`, `lex:bank#money`, `gram:adj-endings`, `cx:lust-haben-auf` |
| `kind` | `lemma` · `grammar` · `construction` |
| `cefr_level` | A1…C2 |
| `payload` | type-specific JSON: article, plural, translations, examples; for grammar, the reference text and the **allowed diagnostic tags** |
| `interference` | JSON: different Italian gender, false friend, similarity |
| `frequency_rank` | from `wordfreq`, for lemmas |
| `curriculum_version` | hash of the source file |

**`item_prerequisites`** `(item_id, requires_item_id)`: graph edges (R§5).

For lemmas, the **two directions** (recognition / production) are not separate items in
the curriculum but **two memory tracks** in the projection (`facet = recognition | production`).

### 4.2 Learner state

**`learner`**: a single row in v1. Known languages, current level, settings (weekly
limits, retention target, evidence weights).

**`learner_items`**: lifecycle of an item for the learner (R§7.4).

| Field | Notes |
|---|---|
| `status` | `unseen` · `presumed_known` · `candidate` · `introduced` · `suspended` |
| `candidate_source` | `optin` · `article` · `wordlist` (determines priority) |
| `candidate_since`, `introduced_at` | |

### 4.3 Activities

- **`texts`**: articles and generated texts. URL, original text, language, simplified
  versions (`text_versions`: level, text, measured coverage, attempts).
- **`exercises`**: type (`flashcard`, `translation`, `guided`, `transform`, `free`,
  `reading_summary`), the prompt shown, reference solution (if any), `targets`
  `[(item_id, facet, weight)]`, reference to the text, version of the LLM prompt that
  generated it.
- **`attempts`**: answer, time taken, help used (hint, glossary).
- **`evaluations`**: structured evaluation of an attempt (§6.2). An evaluation is never
  modified: an accepted contest creates a new evaluation with `supersedes = <id>`.
- **`contests`**: contested evaluation, reason, status, resolution, resolver used.

### 4.4 Event log and projections

**`learning_events`**: append-only.

| Field | Notes |
|---|---|
| `id`, `ts` | |
| `item_id`, `facet` | |
| `kind` | `review` (explicit exercise) · `implicit` (correct use in a sentence, reading) · `lookup` (glossary tapped) · `introduce` · `status_change` |
| `outcome` | `correct` · `assisted` · `error` |
| `evidence_weight` | from the exercise type (R§4.2) |
| `diagnostic_tags` | for errors on grammar points (R§4.1) |
| `evaluation_id` | the evaluation it derives from |
| `voided_by` | null, or the id of the evaluation that replaces it |

**`item_memory`**: a **projection**, rebuildable at any time.

| Field | Notes |
|---|---|
| `item_id`, `facet` | |
| `fsrs_state` | stability, difficulty, last review, due date |
| `mastery`, `n_effective` | weighted moving average and effective number of observations |
| `tag_error_counts` | JSON `{tag: count}` with decay |
| `projection_version` | version of FSRS and of the parameters used |

**Recomputation.** `replay(item_id, facet)` re-reads the non-voided events in `ts` order
and re-applies the pure domain functions. It is used:
- incrementally: a new event updates the projection without re-reading everything;
- selectively: after a contest, the affected items are reprocessed;
- in bulk: after a change to FSRS parameters or the mastery model (`replay_events` script).

The projection must be **deterministic**: the same events and configuration produce the
same state. Tests verify this.

### 4.5 Other tables (as built)

| Table | Milestone | Purpose |
|---|---|---|
| `learners` | M0/M1 | level, known languages, explanation language, settings JSON |
| `exercises`, `attempts` | M1/M2 | cards and answers; production exercises have `status` `pending → ready → answered / failed` |
| `evaluations` | M2/M4 | immutable gradings (also synthetic ones for flashcards); `supersedes` |
| `remediation_queue` | M2 | items to re-practise after systematic errors |
| `explanations` | M2 | cached error explanations |
| `texts` (model `SourceText`), `text_versions`, `reading_sessions`, `glosses` | M3 | reading |
| `contests`, `placements` | M4 | contests and placement tests |

`learning_events` also carries `presumed_known`, `confidence`, `context` (`placement`) and
`predicted_retrievability` (M5, for calibration).

### 4.6 LLM log

**`llm_calls`**: task, prompt version, model, input, output, tokens, latency, outcome
(including `stop_reason`). This is the basis for the grader evaluation dataset (R§8).

**`app_settings`** (M9): `key`, `value` (JSON), `updated_at`. App-level operator configuration,
not learner state (no events). One key so far: `llm_overrides` (§7.1.1).

## 5. Domain (pure functions)

### 5.1 Mastery
An exponential moving average weighted by strength of evidence:

```
m ← m + α · w · (x − m)          x ∈ {1 correct, 0.5 assisted, 0 error}
n_eff ← λ · n_eff + w            λ < 1: older observations count less
```

`α`, `λ` and the weights `w` per exercise type live in configuration. The `MasteryModel`
interface allows replacing it later (e.g. Beta with decay) and reprocessing the history.

### 5.2 From outcome to FSRS grade (R§4.3)
`grade(outcome, mastery, n_eff, confidence) → Rating | None`

- `None` = no FSRS update. Used for uncertain evaluations; mastery is still updated, with
  reduced weight.
- Error with `mastery ≥ threshold` and `n_eff ≥ minimum` → `Hard`; otherwise `Again` plus
  a `needs_remediation` flag.

### 5.3 Scheduling
`py-fsrs` is used for scheduling. The retention target is configurable, default 0.85
(R§10). Implicit reviews go through the same scheduler: FSRS already handles early reviews.
The scheduler runs **without learning steps** (items are introduced inside exercises, not
drilled within a session) and **without fuzzing** (projections must be deterministic); card
ids are derived from `(item_id, facet)`. Fitted FSRS parameters (M5) are part of the
projection config, so changing them changes `projection_version` and triggers a replay.

### 5.4 New-item budget (R§7.4)
`new_item_budget(events_last_7d, backlog, settings) → {lemmas: n, grammar: n}`.
Rolling 7-day window with no carry-over, reduced in proportion to the backlog.

### 5.5 Progress (M8, `app/domain/progress.py`)
Study days and streaks in the learner's time zone (`timezone` setting), activity per day, memory
states (`learning` < 1 day stability, `young` < 21 days, `mature` ≥ 21), due forecast, progress
per CEFR level, and the **mastery trajectory** of an item: its non-voided events replayed with
`projection.apply`, recording the state after each one. Study days come from attempts and
finished readings (a contest never removes a study day); placement counts for item state only.
Details: `docs/design/M8-progress.md`.

### 5.6 Classifying words met while reading (R§7.3)
`classify(lemma, learner_state, current_level) → known | presumed_known | auto_candidate | optin | ignore`.
Proper nouns and transparent compounds with known parts go to `ignore` (or point to their parts).

## 6. Main flows

### 6.1 Review session
1. `session_builder` chooses the items to cover:
   - due items, ordered by retrievability and importance, up to the cap;
   - if fewer are due than the cap, not-yet-due items practised ahead of schedule (lowest
     retrievability first) fill the remaining flashcards and production slots;
   - new items, taken from the candidate queue within the weekly budget.
2. For each group of items:
   - due lemmas → flashcards;
   - due grammar points and new items → a generated production exercise (§6.2) that
     **combines** them. An exercise introduces new words by using them, with a gloss.
3. Answer → evaluation → events → projection update → feedback.

### 6.2 Generating and grading a production exercise

**Generation** (`llm.generate_exercise`)
- Input: target items, known vocabulary (sample), level, exercise type, instruction
  language (IT).
- Structured output: prompt for the learner, reference solutions, `targets` with weights.
- A post-generation check (lemmatization) verifies the prompt doesn't use unknown words
  beyond the intended ones.

**Grading** (`grading_service`)
1. **LanguageTool** on the answer: deterministic morphology and agreement errors.
2. **LLM** (`llm.grade_sentence`). It receives the exercise, the answer, the target items,
   the allowed diagnostic tags and the LanguageTool result. It returns a structured schema:

```json
{
  "overall": "correct | minor_errors | major_errors | off_task",
  "accepted_variants_note": "…",
  "errors": [
    {"span": [12, 18], "original": "die Tisch", "correction": "den Tisch",
     "item_id": "gram:accusative-articles", "diagnostic_tags": ["acc", "masc"],
     "severity": "major", "confidence": 0.9}
  ],
  "correct_uses": [{"item_id": "lex:tisch", "facet": "production"}],
  "corrected_sentence": "…",
  "feedback_it": "…"
}
```

3. **Reconciliation:**
   - LanguageTool and LLM agree → full confidence;
   - they disagree → reduced `confidence`, and the items are marked uncertain (R§8);
   - `item_id`s not in the curriculum are discarded and logged.
4. The `evaluation` is saved, events are emitted (errors → `review/error`, correct uses →
   `implicit/correct` or `review/correct`), and the projection is updated.
5. If `needs_remediation` is set, `llm.explain` produces an explanation targeted at the
   diagnostic tag and anchored to the item's reference text (R§9), and 1–2 remedial
   exercises are queued.

### 6.3 Reading
1. **Ingestion:** URL → extraction with `trafilatura`; if that fails, pasted text.
2. **Analysis:** spaCy (`de_core_news_md` or similar) for lemmas and POS, compound
   splitting, word classification (§5.6).
3. **Simplification** (`llm.simplify_text`). It receives the text, the target level, the
   list of allowed words (known + candidates) and a reference style. Then:
   - coverage is measured;
   - if it is below the threshold (R§7.3), the text is regenerated with the words to
     replace listed, for at most N attempts;
   - the best version is then accepted, with its actual coverage shown.
4. **Reading** with tap-to-gloss (`llm.gloss`, cached per lemma and context).
   A tap emits a `lookup` event.
5. **End of reading:**
   - `implicit` events for known items that were not looked up (with low weight);
   - candidates enter the queue;
   - a summary or comment is offered: a `reading_summary` exercise, graded as in §6.2.

### 6.4 Contest (R§8)
1. The learner contests an evaluation → `contests` (status `open`).
2. `ContestResolver.resolve(contest) → Resolution`.

```python
class ContestResolver(Protocol):
    def resolve(self, contest: Contest, evaluation: Evaluation) -> Resolution: ...

@dataclass
class Resolution:
    verdict: Literal["accepted", "rejected", "partial"]
    replacement: EvaluationResult | None   # new evaluation, if it changes
    rationale: str
```

   v1 ships `AcceptAllResolver`: verdict `accepted`, with a replacement evaluation in
   which the contested errors are removed and the related items count as `correct_uses`.
3. **Application:**
   - a new `evaluation` with `supersedes`;
   - old events marked `voided_by`;
   - new events emitted;
   - `replay` of the affected items.
4. Everything stays in the log: contests are the most valuable material for evaluating
   the grader.

### 6.5 Initial assessment (R§6)
1. The declared level (A2–B1) sets `presumed_known` on items from lower levels.
2. A short test refines the estimate:
   - a sample of lemmas per frequency band (recognition);
   - 3–5 guided sentences on key grammar points, especially the word-order stages (R§5).
3. The outcomes become events like any other: placement is just initial history.

## 7. LLM layer

### 7.1 Interface
Services use **typed tasks**, not generic calls:

```python
class LLMClient(Protocol):
    def generate_exercise(self, req: ExerciseRequest) -> GeneratedExercise: ...
    def grade_sentence(self, req: GradeRequest) -> GradeResult: ...
    def simplify_text(self, req: SimplifyRequest) -> SimplifiedText: ...
    def explain(self, req: ExplainRequest) -> Explanation: ...
    def gloss(self, req: GlossRequest) -> Gloss: ...
```

Inputs and outputs are Pydantic models. Implementations live in `app/llm/`, one small adapter
per provider on its official SDK: `AnthropicLLMClient` (`anthropic`), `OpenAICompatibleLLMClient`
(`openai`; serves OpenAI and OpenRouter) and `GeminiLLMClient` (`google-genai`). A
`FakeLLMClient` is used in tests. No multi-provider abstraction library is used (R§11).

### 7.1.1 Providers and routing (M6)
- **Configuration.** `LLMLL_LLM_PROVIDER` (default provider), `LLMLL_LLM_MODEL` (its model) and
  `LLMLL_LLM_TASKS` (per task `provider`, `model`, `effort`, `max_tokens`, `params`,
  `structured_output`). Resolution per task: task setting, global default, built-in default
  (Anthropic only: `claude-opus-5-5`). Any other provider needs an explicit model; a missing model
  or a missing key for a provider in use is a startup error (`LLMConfigError`). With no key at all
  the whole app uses `FakeLLMClient`.
- **`factory.py`** builds one client per provider in use. If all tasks go to one provider that
  client is returned as is; otherwise a `RoutingLLMClient` dispatches each task to its provider.
  Clients expose `name` and `routes` (`{task: "provider/model"}`), reported by `/api/health` as
  `llm` and `llm_tasks`.
- **Shared adapter code** (`base.py`): prompt loading (the same versioned prompts for every
  provider, stable part first so automatic prefix caching works), `native` or `json` structured
  output (JSON schema in the system prompt, Pydantic validation, one retry with the validation
  error), error mapping (rate limit, connection, 5xx to `LLMUnavailable`; other API errors to
  `LLMError`; refusal or safety block to `LLMRefusal`; truncation to `LLMError`) and `llm_calls`
  logging, which records `provider` too.
- **Native structured output:** OpenAI-compatible `chat.completions.parse(response_format=<model>)`;
  Gemini JSON mime type plus `response_schema=<model>`. Response models must stay convertible by
  both converters (all fields required or defaulted, no unions of objects, no free-form dicts);
  `tests/test_llm_schemas.py` checks it.
- **Caching.** Only Anthropic gets explicit `cache_control`; cached prompt tokens are logged when
  the provider reports them.
- **Settings overrides (M9).** The provider and model of each task can be chosen from the
  Settings page. Precedence, weakest first: built-in defaults, environment, Settings overrides
  (table `app_settings`, key `llm_overrides`; an operator setting, not learner state, so no event),
  CLI flags of one command. `app/llm/overrides.py` holds the pure merge (`apply_overrides`; when
  the provider changes, the environment's `effort`/`params`/`structured_output` are dropped) and
  the load/save. `create_app` keeps a builder in `app.state.llm_builder`; `PUT /api/settings/llm`
  builds the whole merged client first (422 with the `LLMConfigError` message on failure, nothing
  stored), saves, then swaps `app.state.llm` atomically. API keys are never sent to or accepted
  from the browser. A stored override whose key disappeared from `.env` falls back to the
  environment at startup (`llm_override_error`, shown in Settings). `check-llm` and `eval-grader`
  apply the stored overrides too. Design: `docs/design/M9-model-settings.md`.

### 7.1.2 Observability (M7)
Design: `docs/design/M7-observability.md`.
- **HTTP tracing.** The SDK clients are built with our own HTTP client carrying request/response
  event hooks (`app/llm/trace.py`; `httpx2` for `openai` and `anthropic`, `httpx` for
  `google-genai`). The adapter sets a `ContextVar` trace around the SDK call; the hooks append
  one entry per HTTP attempt (request time, headers time, status). `derive_timing` (pure) turns
  it into `attempts`, `http_statuses`, `retry_wait_ms`, `ttfb_ms`, `download_ms` and
  `overhead_ms`. Hooks never raise; the `LLMClient` protocol is unchanged.
- **Call record.** `CallRecord` and `llm_calls` also carry `reasoning_tokens`, `upstream_provider`
  (OpenRouter) and `request_chars`; all new columns are nullable (migration `0008`).
  `LLMLL_LLM_TIMEOUT_S` and `LLMLL_LLM_MAX_RETRIES` configure the clients.
- **Reading the numbers.** `eval-grader` prints a breakdown per run and a latency block (kept in
  the saved report; `eval-compare` shows p50/p90, ttfb, retries, reasoning tokens);
  `llm-stats` aggregates `llm_calls` (`app/domain/llm_stats.py`, pure); `check-llm --call
  --repeat N` probes with tiny requests.
- **Live debug.** With `LLMLL_DEBUG=true`, `SqlCallRecorder` publishes `call_started` /
  `call_finished` to the in-process `LiveBroadcaster` (`app/llm/live.py`: a 200-event ring buffer
  and one bounded `asyncio.Queue` per SSE subscriber, handed over with `call_soon_threadsafe`
  because LLM calls run in worker threads). `GET /api/debug/llm/events` streams them as
  Server-Sent Events and `GET /api/debug/llm/calls` lists `llm_calls` rows; both are 404 when
  debug is off, and the access token applies. Nothing is stored or buffered when it is off.

### 7.2 Claude API implementation
- **Structured output.** Use `client.messages.parse()` with the Pydantic model as the
  schema, instead of parsing free text.
- **Model.** Configurable **per task** (`LLMLL_LLM_TASKS`). Default for all tasks: `claude-opus-5-5`.
- **Refusals.** Server-side fallback is enabled (`fallbacks="default"` via the beta messages
  namespace, setting `llm_refusal_fallback`); a final refusal raises `LLMRefusal`.
- **No API key.** The app falls back to `FakeLLMClient` and the UI shows a banner.
  Using cheaper models for simple tasks (gloss, generation) is a decision to make after
  measuring them on the evaluation dataset, not up front.
- **Effort.** Configurable per task. High for grading (correctness matters), lower for
  gloss and generation.
- **Prompt caching.** Stable parts go at the start of the prompt: system instructions,
  the grammar point's reference text, the learner profile. Variable parts (answer, text)
  go at the end. Especially useful for sessions with several exercises on the same item.
- **Refusals and errors.** Always check `stop_reason` before reading the content.
  API errors are handled by distinguishing retryable ones (429, 5xx) from the rest.
- **Batch API.** Used for offline work, at reduced cost: curriculum drafts,
  pre-generated examples.
- **Prompts** live in `app/llm/prompts/*.vN.md`. The version is recorded in
  `llm_calls` and `evaluations`.

## 8. NLP layer

| Component | Tool | Notes |
|---|---|---|
| Lemmas, POS, proper nouns | spaCy with `de_core_news_md` (pinned in `uv.lock`) | Reliable but imperfect lemmatization; separable-verb particles are not reattached |
| Compounds | own dynamic-programming splitter over lexicon lemmas with linking elements (`s`, `es`, `n`, `en`, `e`) | Simpler to maintain than a library for this narrow use |
| Frequencies | `wordfreq` | Rank per lemma |
| Grammar checking | LanguageTool, server in a container | Called over local HTTP |
| Article extraction | `trafilatura` | |

## 9. Curriculum: YAML format

```yaml
# curriculum/de/lexicon/a2.yaml
- id: lex:tisch
  lemma: Tisch
  pos: noun
  gender: m
  plural: Tische
  level: A1
  translations: {it: tavolo, en: table}
  interference: {it_gender: m}          # same gender: no warning
- id: lex:sonne
  lemma: Sonne
  pos: noun
  gender: f
  plural: Sonnen
  level: A1
  translations: {it: sole, en: sun}
  interference: {it_gender: m}          # different → priority and warning
```

```yaml
# curriculum/de/grammar/adj-endings.yaml
id: gram:adj-endings
title_it: Desinenze dell'aggettivo
level: A2
requires: [gram:cases-overview, gram:definite-articles]
diagnostic_tags:
  case: [nom, acc, dat, gen]
  gender_number: [masc, fem, neut, plural]
  declension: [strong, weak, mixed]
reference_it: |
  Curated reference text: rules, table, examples, typical errors of Italian speakers.
```

User-facing fields (`title_it`, `reference_it`) are in Italian, the explanation language
for v1; the `_it` suffix leaves room for other explanation languages.

The `import_curriculum` script:
- validates the schema;
- checks that ids are unique and that prerequisites exist, with no cycles;
- updates `items` without touching learner state.

Removed ids become `suspended`; they are not deleted.

## 10. Frontend

- **Views:** Session (flashcards + exercises), Reading (text with tap-to-gloss), Progress
  (overview, levels, grammar/words with item history, lessons taken; it replaced the former
  Corpus view), Grammar (reference + on-demand explanations), Settings. On wide screens only,
  when the backend runs with `LLMLL_DEBUG=true`: Debug (live LLM exchanges, also as a drawer).
- **Charts:** small inline-SVG components, no chart library.
- **PWA:** manifest and service worker so it can be installed on a phone. No offline
  mode in v1, because grading requires the LLM.
- **Phone first:** 10-minute sessions designed for a small screen.
- The API client is generated from FastAPI's OpenAPI schema (e.g. `openapi-typescript`),
  so the types stay in sync.

## 11. Testing and quality

- **Domain:** unit tests on the pure functions (mastery, grading, budget, classification).
- **Replay determinism:** tests that rebuild the projections from the events and compare
  them with the incremental state.
- **Services:** tests with `FakeLLMClient` and a mocked LanguageTool.
- **Grader evaluation:** `scripts/eval_grader.py` runs the grader on a set of annotated
  sentences (and on the contests). It measures false positives and false negatives per
  item. Run it on every prompt or model change.

## 12. Deployment

- `docker compose up`:
  - `backend`: FastAPI, which also serves the built frontend;
  - `languagetool`;
  - a volume for SQLite.
- Configuration via environment variables (`ANTHROPIC_API_KEY`, paths, parameters).
- Backups: periodic copy of the SQLite file. The curriculum is already in git.
- Local or on a small VPS. Installing the PWA on a phone requires HTTPS (e.g. a reverse
  proxy with automatic certificates).
- Access control: a single access token (`LLMLL_ACCESS_TOKEN`); see the root README's
  deployment section. Without it the app is open, which is only acceptable locally.
- On startup the container runs migrations, then `import-curriculum`, then the server.

## 13. Milestones

All milestones are implemented; designs and deviations are in `docs/design/M*.md`.

| Milestone | Content | Usable result |
|---|---|---|
| **M0** | Skeleton: repo, compose, FastAPI, React, DB, migrations, CI with tests | Empty app that starts |
| **M1** | Curriculum (YAML import, A1–B1 vocabulary), event log, projections, FSRS, flashcards | Flashcards with scheduling |
| **M2** | LLM layer, production exercises, grading (LLM + LanguageTool), explanations | Complete review session |
| **M3** | Reading: ingestion, simplification with coverage, glossary, candidates | Reading articles |
| **M4** | Candidate queue and weekly budget, contests, initial assessment | Full requirements loop |
| **M5** | Grader dataset and evaluation script, FSRS optimisation | Quality measurement |
| **M6** | Multiple LLM providers (Anthropic, OpenAI, Gemini, OpenRouter), per-task routing | Provider choice and comparison |
| **M7** | LLM observability: per-call timing breakdown, `llm-stats`, live debug pane | Diagnosing slowness |
| **M8** | Progress review: streaks, activity, levels, item history, lessons taken | Seeing progress |
| **M9** | LLM provider and model per task chosen from Settings (stored overrides, live switch) | Changing models without restarting |

## 14. Open decisions

- Granularity of implicit events from reading: weight (currently 0.2) and cap (30 per text).
- Compound splitter and spaCy `md` model: evaluate recall/accuracy on real articles.
- Grader quality with the real model: run `eval-grader` (M5) and tune prompts before
  trusting gradings; the key metric is the false-positive rate on correct answers.
- Model choice per task (e.g. a cheaper model for gloss and generation) — decide after
  measuring with `eval-grader`, not up front.
- Starting values for `α`, `λ`, evidence weights and grading thresholds: to be tuned
  through use (the event log makes it possible to recompute everything).
