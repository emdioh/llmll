# LLMLL — Architecture

> Living document, companion to [`REQUIREMENTS.md`](REQUIREMENTS.md): references of the
> form `R§n` point to sections of the requirements.
> Status: draft v0.2 (October 2026).

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
├── REQUIREMENTS.md  ARCHITECTURE.md
├── docker-compose.yml          # backend + languagetool (+ frontend as a static build)
├── curriculum/de/              # content, versioned in git
│   ├── lexicon/a1.yaml a2.yaml b1.yaml
│   ├── grammar/*.yaml          # one file per grammar point, with reference text
│   └── constructions.yaml
├── backend/
│   ├── pyproject.toml
│   ├── app/{api,services,domain,llm,nlp,store}/
│   ├── app/llm/prompts/        # versioned templates (e.g. grade_sentence.v3.md)
│   ├── migrations/             # Alembic
│   ├── scripts/                # import_curriculum, replay_events, eval_grader
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

### 4.5 LLM log

**`llm_calls`**: task, prompt version, model, input, output, tokens, latency, outcome
(including `stop_reason`). This is the basis for the grader evaluation dataset (R§8).

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

### 5.4 New-item budget (R§7.4)
`new_item_budget(events_last_7d, backlog, settings) → {lemmas: n, grammar: n}`.
Rolling 7-day window with no carry-over, reduced in proportion to the backlog.

### 5.5 Classifying words met while reading (R§7.3)
`classify(lemma, learner_state, current_level) → known | presumed_known | auto_candidate | optin | ignore`.
Proper nouns and transparent compounds with known parts go to `ignore` (or point to their parts).

## 6. Main flows

### 6.1 Review session
1. `session_builder` chooses the items to cover:
   - due items, ordered by retrievability and importance, up to the cap;
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
   splitting, word classification (§5.5).
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

Inputs and outputs are Pydantic models. The `AnthropicLLMClient` implementation uses the
official `anthropic` Python SDK. A `FakeLLMClient` with recorded responses is used in tests.

### 7.2 Claude API implementation
- **Structured output.** Use `client.messages.parse()` with the Pydantic model as the
  schema, instead of parsing free text.
- **Model.** Configurable **per task**. Default for all tasks: `claude-opus-5-5`.
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
| Lemmas, POS, proper nouns | spaCy, German model | Reliable but imperfect lemmatization: errors are logged |
| Compounds | splitting library (candidate: CharSplit) + check of the parts against the lexicon | To be evaluated |
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

- **Views:** Session (flashcards + exercises), Reading (text with tap-to-gloss),
  Corpus (item state, filters), Grammar (on-demand explanations), Settings.
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

## 13. Milestones

| Milestone | Content | Usable result |
|---|---|---|
| **M0** | Skeleton: repo, compose, FastAPI, React, DB, migrations, CI with tests | Empty app that starts |
| **M1** | Curriculum (YAML import, A1–B1 vocabulary), event log, projections, FSRS, flashcards | Flashcards with scheduling |
| **M2** | LLM layer, production exercises, grading (LLM + LanguageTool), explanations | Complete review session |
| **M3** | Reading: ingestion, simplification with coverage, glossary, candidates | Reading articles |
| **M4** | Candidate queue and weekly budget, contests, initial assessment | Full requirements loop |
| **M5** | Grader dataset and evaluation script, FSRS optimisation | Quality measurement |

## 14. Open decisions

- Granularity of implicit events from reading: weight, and how many items per text.
- Compound-splitting library: to be evaluated on a sample.
- spaCy model: `md` or `lg`, depending on lemma accuracy on a sample.
- Starting values for `α`, `λ`, evidence weights and grading thresholds: to be tuned
  through use (the event log makes it possible to recompute everything).
