# LLMLL — Requirements

> Living document. It records the decisions made so far and the questions still open.
> Status: draft v0.5 (October 2026).

## 1. Vision

A language-learning app that combines:
- a **structured curriculum** (grammar and vocabulary in a sensible order),
- **spaced repetition** over *everything* the learner studies,
- an **LLM** that generates exercises and texts, grades free-form sentences and explains grammar.

The core idea: **a single model of what the learner knows**. Flashcards, sentences to
produce and texts to read are just different ways of testing and updating that model.

## 2. Scope

### v1: personal use
- **Target language:** German.
- **Known languages:** Italian (native) and English. Explanations are in Italian.
- **Starting level:** A2–B1, probably with uneven gaps.
- **Available time:** 1–2 hours a week, in sessions of about 10 minutes, not every day.
- **Platform:** web, usable on a phone (responsive / PWA).

### Out of scope for v1
- Speaking, listening, pronunciation (no TTS or speech recognition).
- Multi-user accounts, payments, other languages.

### Later: commercial product
The choices made for v1 must not rule it out (see §13).

## 3. Principles

1. **Everything is corpus.** Words, grammar and constructions are *knowledge items*, each with a memory state.
2. **Successes count, not just errors.** Every correct use is evidence of knowledge.
3. **One error does not erase a history of successes.** A slip is not a systematic error.
4. **The grader can be wrong.** Its evaluations carry a confidence level and can be contested.
5. **Content must be interesting.** With little time available, motivation is the bottleneck.
6. **Content is data, not code.** Curriculum, items and exercises live in files or the database, not in code.

## 4. Knowledge model

### 4.1 Knowledge items
Types:
- **Lemma:** a lexical entry with one specific meaning. Nouns always include article and plural (`der Tisch, -e`).
  Different meanings are different items (`die Bank` bench / bank).
- **Grammar point:** e.g. "dative after `mit`", "verb-final in subordinate clauses", "adjective endings".
- **Construction / collocation:** e.g. `Lust haben auf + Akk`, `es gibt + Akk`.

Per-item metadata:
- indicative CEFR level, frequency;
- prerequisites (graph edges, §5);
- **interference with known languages**: e.g. gender differs from Italian
  (`il sole → die Sonne`, `il mare → das Meer`), false friends (`bekommen ≠ become`),
  useful similarities (`haben/sein` ≈ `avere/essere` in the Italian *passato prossimo*);
- for lemmas, the **direction**: recognition (DE→IT) and production (IT→DE) are separate
  memory states.

**Granularity rule: coarse items, fine diagnostic tags.**
A grammar point is a single item for curriculum and scheduling (e.g. "adjective
endings"). Each error, however, carries **diagnostic tags** for the sub-dimension
involved; for adjective endings: case × gender/number × declension type
(strong / weak / mixed). The tags are used for:
- explanations targeted at the specific sub-case, not the whole table;
- remedial exercises on that sub-case;
- telling "doesn't know the endings" apart from "only gets feminine dative wrong".

If a sub-case turns out to be systematically wrong, it can be promoted to an item of its
own (or a sub-item) without changing the model.

### 4.2 Two levels of state per item

| Level | Question | Model |
|---|---|---|
| **Memory** | When should it be reviewed? | FSRS (stability, difficulty, retrievability) |
| **Mastery** | Can I use it reliably? Is an error a slip or systematic? | Weighted moving average (v1), possibly Beta with decay later |

**FSRS** (Free Spaced Repetition Scheduler, open source, used by Anki): for each item it
estimates *stability* (days until recall probability drops to 90%), *difficulty* and
current *retrievability*. Each review receives a grade: Again / Hard / Good / Easy.

**Mastery:** a moving average over recent results. Each observation is weighted by:
- **strength of evidence**: recognition flashcard < production flashcard < guided sentence < free sentence;
- **recency.**

v1 must also track the **number of observations**, to tell "3 out of 3" from "30 out of 30".

**Grammar points:** every sentence in which the point appears is an observation for that
item, whatever the context. Mastery is computed on the item. In parallel, an
**error count per diagnostic tag** is kept. It is used to choose explanations and remedial
exercises, and to decide whether to promote a sub-case to an item (§4.1).

### 4.3 From exercise outcome to FSRS grade
The grade is not self-assessed: the app derives it.

| Outcome | Mastery | FSRS grade | Extra action |
|---|---|---|---|
| Correct, without hesitation | any | Good / Easy | — |
| Correct with help (hint, glossary) | any | Hard | — |
| Error | high (history of successes) | **Hard** (slip) | — |
| Error | low or falling | **Again** | targeted explanation + 1–2 remedial exercises |
| Uncertain evaluation (§8) | any | no update, or a dampened update | — |

Thresholds are tuned through use.

### 4.4 Implicit reviews
Using an item correctly in a sentence, or reading it in a text without looking it up,
counts as a review. An early review yields a smaller stability gain (FSRS already handles
this). Many flashcards will never need to be shown, because the item was already
"reviewed" by using it.

## 5. Curriculum

- **Prerequisite graph**, not a linear sequence. Items can be "tested out of", and the
  path adapts.
- **Word order follows the documented acquisition sequence** (ZISA project,
  Clahsen/Meisel/Pienemann, studied specifically on Italian and Spanish learners):
  1. canonical SVO
  2. adverb fronting
  3. separable-verb particle split / verbal bracket
  4. inversion (V2)
  5. verb-final in subordinate clauses
- **Reference sources for v1:** Profile Deutsch, Goethe A1–B1 word lists
  (about 650 / 1300 / 2400 entries). The word list assigns a CEFR level to each lemma: it
  is the reference for deciding what is "presumed known", what is a candidate and what is
  opt-in. Levels B2+ need another source (e.g. frequency ranks mapped to levels).
  ⚠️ These sources are copyrighted: fine for personal use, but a commercial product needs
  its own curriculum (§13).
- The curriculum is built **offline** (with LLM assistance, then reviewed); the LLM does
  **not** invent it during sessions.
- **Format:** YAML files in the repo, one per area (vocabulary per level, grammar points,
  constructions). Each entry has: a stable id, level, prerequisites, interference tags, and
  for grammar points a short reference text (§9). A script validates them and imports them
  into the database. The LLM writes drafts, a human reviews them; git provides the history.

## 6. Initial assessment

Starting from A2–B1, hundreds or thousands of items are already known. **They must not all
be queued for review**: that would produce an avalanche of flashcards.

- Items below the estimated level start as **"presumed known"**: a prior state, not scheduled.
- They enter scheduling only when: (a) an error shows up, (b) random sampling checks them,
  (c) they are used or read, i.e. an implicit review.
- **Short initial test:** vocabulary-size estimate by frequency band, plus 3–5 sentences to
  produce, evaluated against the graph.
- Every session refines the estimate, so a wrong placement corrects itself within a few days.
- Recognition and production are estimated separately.

## 7. Activities

### 7.1 Production exercises
From most to least constrained:
1. **IT→DE translation:** the meaning is fixed, so it is the easiest to grade.
2. **Guided sentence:** "use X to say Y" (e.g. "use `weil` to explain why you are late").
   This counters *avoidance*: without constraints, learners steer around structures they don't know.
3. **Rephrasing / transformation:** e.g. main clause to subordinate clause, present to Perfekt.
4. **Free production:** on a topic, or in response to a text just read (§7.3).

Each exercise declares which items it tests and with what weight.

### 7.2 Flashcards
- Lemmas with article, plural and an example sentence.
- Both directions, scheduled separately.
- Priority to words whose gender differs from Italian.

### 7.3 Reading: the motivation engine
This is the central feature, not an extra.

- **Sources:**
  - the app accepts **a link or pasted text**. If extraction from the link fails
    (paywall, dynamic page), the learner is asked to paste the text.
  - **Initial topics:** current affairs, technology, science (to be refined).
  - Specific sources: not relevant for now. RSS feeds may come later.
  - Original German articles are preferred (authentic German); translating Italian or
    English articles is the alternative.
- **Simplification to the learner's level**, with **measured lexical coverage**: after
  lemmatization (e.g. spaCy `de`), at least 95–98% of tokens must already be known.
  If the LLM misses the threshold, the text is regenerated. New words are few and chosen.
- **Tap-to-gloss:** in-context translation, lemma, gender, plural. Tapping a word is a
  signal: for that item it counts as a failed or assisted review.
- **Words met while reading** are classified against the **current CEFR level**
  (according to the reference word list, §5):
  - already in the corpus or "presumed known" (lower level) → **implicit review**, not a new word;
  - current level, not yet in the corpus → **automatic candidate** (§7.4);
  - higher level or missing from the word list → **opt-in** (one tap).
  - **Excluded:** proper nouns; **transparent compounds** (`Klimaschutzgesetz`) whose
    parts are already known: they are split, and the parts are reviewed, not the compound.
- **After reading:** 1–2 comprehension questions and/or a short summary or comment to
  write. This way reading feeds the production exercises.
- **Texts generated from scratch**, using the words due for review, as an alternative when
  there is no article.

### 7.4 Introducing new words
New words come in **through exercises**, not as lists to study: a word is "introduced"
when it first appears in an exercise (guided sentence, translation, generated text), with
a gloss.

- **Word states:** `candidate` → `introduced` → under review (FSRS).
  Being a candidate costs nothing; the cost, in future reviews, starts at introduction.
- **Candidate queue, by priority:**
  1. explicitly chosen words (opt-in);
  2. current-level words met in articles;
  3. current-level words from the word list, in curriculum and frequency order.

  Exercises draw from the queue: if articles don't supply enough new material, the word
  list fills the gap.
- **Configurable weekly limit** on introduced words (rolling 7-day window,
  **no carry-over**: skipping a week does not double the next one). Indicative starting
  value: ~20/week.
- The limit is **reduced automatically when there is a review backlog**.
- **A separate, lower limit for new grammar points** (indicatively 1–2 per week).
- Excess candidates stay in the queue. There is no per-article cap, since the weekly limit
  already regulates intake.

## 8. Grading

- **Structured output** from the LLM. For each error:
  - span;
  - correction;
  - curriculum item involved;
  - severity;
  - confidence.

  *Correct* uses of items are reported too (§4.4).
- **Don't correct everything.** Errors on the items being practised, or systematic ones,
  come first; others, if any, go in a secondary area.
- **Accept variants.** Multiple correct answers are normal, and stylistic preferences are
  not errors.
- **Double check:** LanguageTool (open source, good German rules) as a deterministic check
  of morphology and agreement. When LanguageTool and the LLM disagree, the evaluation is
  **uncertain** and weighs less on the state.
- **"I think I was right" button (contest):**
  - the contest goes to a **`ContestResolver`**, an extension point with a fixed
    interface: it receives the contested evaluation and returns
    *accepted / rejected / partial* (optionally with a corrected evaluation);
  - **v1: an "always accept" implementation.** The memory and mastery update caused by the
    error is reverted, and the answer counts as a correct use;
  - later: re-grading with a stronger model, checking with LanguageTool, a review queue
    for a native speaker;
  - every contest and its resolution stays in the log: these are the most valuable cases
    for measuring the grader.
  - To make reverting possible, item state is recomputed from **events** (event log),
    not just overwritten.
- **Full log** of every evaluation (input, output, prompt and model version).
  It becomes the dataset for measuring the grader; to be spot-checked by a native speaker.

## 9. Explanations

- **On errors:** short, tied to the item. Expandable.
- **On demand:** for every grammar point.
- **Anchored to a curated reference text** for each grammar point, to reduce LLM-invented
  rules.
- **Consistent with the learner's path:** they don't use concepts the learner hasn't met yet.
- **Leverage known languages:** analogies with Italian (`avere/essere` ↔ `haben/sein`) and
  English (lexical cognates), warnings about interference.

## 10. Sessions and load

With 1–2 hours a week, used irregularly, the main problem is **backlog**.

- **Sessions of about 10 minutes, interruptible and resumable.** No penalty for skipping a day.
- **Lower FSRS retention target** (e.g. 85% instead of 90%) to reduce load; adjustable.
- **Cap on reviews per session**, prioritised by:
  - low retrievability;
  - item importance (frequency, prerequisite of other items).
- **Introduction of new items** governed by the weekly limit (§7.4).
- **Two session types**, chosen by the user:
  - **Review (~10 minutes):** due reviews up to a cap (~15), then 1–2 sentences to
    produce, then 2–3 new items if time remains.
  - **Reading (open-ended):** one article, with a short summary or comment at the end.
- The proportions are tuned through use.

## 11. Platform and stack

Criterion: **mainstream, maintainable technology**, nothing exotic.

| Part | Choice | Reason |
|---|---|---|
| Backend | **Python + FastAPI** | The NLP ecosystem for German (spaCy, `py-fsrs`, trafilatura) is in Python |
| Database | **SQLite** via SQLAlchemy (+ Alembic for migrations) | One file, trivial backups; moving to PostgreSQL without rewrites |
| Frontend | **React + TypeScript (Vite)**, as a responsive PWA | The most widespread option; works on a phone without an app store |
| Deterministic checking | **LanguageTool**, self-hosted (Docker container) | The public API has usage limits |
| LLM | Claude API behind our own interface | Replaceable provider |
| Deploy | Docker Compose (app + LanguageTool), locally or on a small VPS | One command to start everything |

LLM cost is negligible for a single user; to be reassessed for the commercial product.

## 12. How to tell whether it works

- Grader accuracy on the annotated dataset (false positives and false negatives).
- Actual retention compared with the FSRS target.
- Progress through the graph over time; an external test now and then (e.g. a Goethe B1/B2 mock exam).
- Real usage: sessions per week, readings completed.

## 13. Preparing for a commercial product

**Do already in v1** (cheap now, expensive later):
- content separated from code;
- language pair as a parameter;
- a "known languages" profile (not a single L1);
- an abstract LLM provider;
- a full event log.

**Postpone:** multi-user, authentication, payments, other languages, UI polish.

**Known issues:**
- **Curriculum copyright** (Profile Deutsch, Goethe lists): an original curriculum is needed.
- **Article copyright:** distributing simplified versions of copyrighted articles creates
  a derivative work. Options:
  - users bring their own URL (processing for personal use);
  - free sources (German Wikipedia, CC BY-SA);
  - agreements with publishers.
- **Crowded market** (Duolingo Max, Babbel, Speak, LLM tutors). Possible differentiators:
  - explanations that leverage the languages the learner already knows;
  - a unified memory model across all activity types;
  - readings adapted to the learner's personal vocabulary.

## 14. Main risks

1. **Grader reliability.** The learner cannot verify it on their own. Mitigations in §8.
2. **Attributing errors to items** (credit assignment) in free sentences.
3. **Cost of building the curriculum.**
4. **Backlog and abandonment** with irregular use.
5. **Text simplification:** the LLM may alter the meaning or ignore the vocabulary
   constraint. Mitigation: coverage measurement and regeneration.

## 15. Open questions

- Word list source for levels B2–C2.
- Starting values for the weekly limits (to be tuned through use).
