# M8 — Progress review: history, streaks, stats, grammar and word details

Design spec. Builds on the event log and projections (M1), production/grading (M2), reading (M3),
contests/placement (M4) and stats (M5 §3). Deviations go at the end of this file.

**Usable result:** a "Progress" page where the learner reviews what they have done (sessions and
readings, with their answers), keeps track of streaks and activity, sees stats per CEFR level and
learning state, and drills into any grammar point or word to see its history and typical errors.

Principle: everything is **derived from the event log and existing tables**; no new source of
truth. Aggregations are pure functions (`app/domain/progress.py`), endpoints only query and call
them. Voided events (contests) are excluded everywhere; placement events (`context =
'placement'`) count for item state but not for streaks/activity.

## 1. Time zone and days
- Learner setting `timezone` (IANA, e.g. `Europe/Rome`) in `learners.settings`; the frontend sets
  it from `Intl.DateTimeFormat().resolvedOptions().timeZone` when missing and on change (Settings
  shows it). Default `UTC`.
- A "study day" is a local calendar day with ≥ 1 learning activity: an answered card or exercise
  (attempt), or a finished reading session.

## 2. Backend

### 2.1 Pure functions (`app/domain/progress.py`)
- `study_days(timestamps, tz) -> set[date]`.
- `streaks(days, today) -> {current, longest, last_study_day}`: the current streak counts back
  from today if today is a study day, else from yesterday (a day without study yet doesn't break
  it until the day is over).
- `activity_by_day(events, tz, start, end) -> [{date, reviews, exercises, new_items, readings,
  minutes}]` (`minutes` from attempts' `duration_ms`, reading sessions' start→finish capped at 60).
- `memory_state(memory, now) -> "new" | "learning" | "young" | "mature" | "presumed_known"`:
  learning = stability < 1 day, young < 21 days, mature ≥ 21 days.
- `due_forecast(memories, now, tz, days) -> [{date, due}]` (overdue counted on day 0).
- `level_progress(items, learner_items, memories) -> per CEFR level and kind: total, introduced,
  mature, presumed_known, mean mastery`.
- `mastery_trajectory(events, cfg) -> [{ts, mastery, stability, outcome, kind}]`: replay the
  item's non-voided events with the existing `projection.apply`, recording the state after each
  event (this is what event sourcing buys: the full history of every item).

### 2.2 Endpoints (`/api/progress/...`)
- `GET summary` → streaks, study days total, totals (reviews, exercises, readings, items
  introduced, minutes), this week vs last week, observed retention (reuse M5 stats), counts by
  memory state, due now / today.
- `GET activity?days=140` → per-day rows (for the calendar heatmap) and weekly accuracy:
  `[{week_start, flashcards_correct_rate, production_correct_rate, n}]`.
- `GET levels` → level progress (§2.1).
- `GET forecast?days=14` → due per day.
- `GET items?kind=grammar|construction|lemma&sort=weakest|strongest|recent|due|errors&q=&level=
  &state=&limit=&offset=` → rows with mastery, state, stability, due, last practiced, n_eff,
  error rate, top diagnostic tags (grammar), facets (lemmas). `weakest` ignores items with too
  little evidence (`n_eff < 2`) unless no other.
- `GET items/{id}` → item curriculum data + learner status + per facet: trajectory (§2.1), counts
  (correct / assisted / error), diagnostic tag error counts, last 10 evaluated answers involving the
  item (exercise prompt, learner answer, corrected sentence, the errors on this item), linked
  explanation if cached.
- `GET history?limit=20&offset=0` → "lessons": review sessions (grouped by `exercises.session_id`)
  and reading sessions, newest first: date, kind, duration, cards answered, correct %, new items,
  for readings the text title and words looked up.
- `GET history/{kind}/{id}` → a session's cards in order with prompt, the learner's answer,
  outcome, expected/corrected answer, errors, contest status; for a reading: the text, lookups,
  the summary exercise and its evaluation.

All list endpoints paginate; all queries are bounded (indexes on `learning_events(learner_id, ts)`
and `attempts(submitted_at)` via migration if missing).

## 3. Frontend
- New nav entry **Progress** (`/progress`), mobile-first. Read the `dataviz` skill before
  writing any chart; charts are small inline-SVG components (no chart library), accessible
  (titles, labels, values on focus/tap), light and dark.
- **Overview** (top of `/progress`): streak (current / longest), this week vs last week
  (study days, reviews, exercises, minutes), retention observed vs target, counts by memory state,
  due today + a 14-day forecast bar strip, a study calendar heatmap (last ~20 weeks), weekly
  accuracy line (flashcards vs written exercises).
- **Levels:** per CEFR level, stacked bar of mature / young / learning / presumed known / not yet,
  separately for words and grammar.
- **Grammar** tab: list of grammar points and constructions with mastery bar, state, last
  practiced, top error tags; sorts (weakest, most errors, recent, due). Detail
  `/progress/items/:id`: mastery-over-time line (with error markers), outcome counts, error tags
  ranked ("dativo femminile ×4"), recent answers with the error highlighted and the correction,
  buttons "Spiegami" (existing explain endpoint) and "Practice this" (opts the item into the
  remediation queue or marks it due — choose the simplest consistent option and document it).
- **Words** tab: searchable list with level/state filters and sorts (weakest, recent, due),
  recognition vs production mastery per word; same detail route.
- **History** tab ("lessons taken"): list of sessions and readings; tapping one shows the cards
  and answers (reuse the feedback components read-only) or the reading.
- Keep it fast on a phone: summary and activity load first, tabs load lazily.

## 4. Tests (must exist)
- Pure functions: streaks (gaps, today not studied yet, time zones around midnight, DST change
  day), activity aggregation, memory states, forecast, level progress, trajectory equals the final
  projection state and skips voided events.
- Endpoints on a seeded DB built through the real API with an injected clock across several days
  (sessions, a production exercise with an error, a reading, a contest): summary, activity,
  items sorts, item detail (trajectory, recent answers), history list/detail; voided events
  excluded; placement excluded from streaks.
- Frontend: overview renders numbers and charts from mocked API; tabs lazy-load; item detail
  shows trajectory and errors; history detail renders answers; timezone set on first load.

## Deviations
_(record any deviation from this spec here, with the reason)_

### Backend (§1, §2, backend tests of §4)

**Pure functions (`app/domain/progress.py`)**
- `memory_state(memory, presumed_known=False)` has no `now` argument: the thresholds depend only
  on stability. An item's state (`item_state`) is that of its **least stable facet** (a word is
  `mature` only when recognition and production both are); an item without a card is
  `presumed_known` when its learner status says so, else `new`. Level progress, summary counts and
  item lists all use this.
- `mastery_trajectory(events, cfg, item_id, facet)` takes the item and facet too (they seed the
  FSRS card id). Points also carry `event_id` and `n_eff`; `status_change` events advance the fold
  but produce no point. The API returns at most the last 500 points per facet (`trajectory_total`
  has the full count); the last point always equals the stored `item_memory` state (tested for every
  memory row of the seeded database).
- `activity_by_day(events, tz, start, end)` takes local dates, returns **every** day of the range
  (empty days included, for the heatmap) and adds `active` (= study day). Events are
  `ActivityEvent(ts, kind, minutes)`: flashcard attempts are `reviews`, production attempts
  (including a reading's summary exercise) are `exercises`, intro cards count as study and minutes
  but as neither; `new_items` come from `learner_items.introduced_at` (null for items first met in
  the placement) and do not make a study day by themselves.
- `due_forecast` counts memories, i.e. (item, facet) rows, not items; suspended items are excluded
  (same rule as the backlog). `level_progress` groups by the real kind (`lemma`, `grammar`,
  `construction`; the frontend sums grammar + construction) and adds `new`, `learning`, `young`
  counts for the stacked bars; `introduced` is the learner status `introduced`.
- Extra pure helpers: `period_totals`, `weekly_accuracy`, `reading_minutes`, `item_state`,
  `item_mastery`.

**Endpoints**
- "This week / last week" are rolling 7-day windows ending today (not calendar weeks), so that
  the comparison is fair on a Monday. The weekly accuracy series uses Monday-based local weeks;
  its rates are the share of `correct` among non-voided `review` events (so a contest changes
  them), split into `flashcards_*` and `production_*` with their own `n`.
- Study days, totals, minutes and streaks come from **attempts** (non-placement sessions) and
  finished readings, so a contest never removes a study day; only the accuracy curve, error counts,
  tags and trajectories use non-voided events. Placement events are included in item state, error
  counts and trajectories, and excluded from streaks, activity, totals and the history.
- `summary` adds `due_today`, `retention.n_reviews`, `streak.studied_today` and state counts split
  into `states_words` / `states_grammar` (constructions count as grammar). The all-time study-day
  set loads one timestamp per attempt (unbounded in principle, cheap in practice); every other
  query is bounded by a window, a page, or a single item.
- `items`: default sort is `recent`. `weakest`/`strongest` rank only items with memory and
  `n_eff >= 2` (all items with memory when none qualifies); `recent`, `due` and `errors` list only
  items that have a last-practiced time, a due date, or at least one error. Item mastery is the
  mean over its facets, `n_eff` the sum, `due` the earliest. Counts and tags come from
  non-voided `review`/`implicit`/`lookup` events (a lookup counts as `assisted`).
- `history` uses `kind` = `session` | `reading`; details are `GET history/session/{session_id}` and
  `GET history/reading/{reading_id}` (two routes instead of one `{kind}/{id}` so that each has a
  precise response model). A reading's summary exercise belongs to the reading entry and is not
  listed as a session. Outcomes shown are those **after contests** (latest evaluation), with the
  contest status attached; intro cards have no outcome and are not part of `correct_rate`.
- **"Practice this"**: `POST /api/progress/items/{id}/practice` adds a `remediation_queue` entry
  (no tags, no evaluation) so that the next session's production exercise targets the item; it is
  idempotent while an entry is pending, refused with 409 for items the learner has not met, and has
  no effect when `production_slots` is 0 (the response reports the slots so the UI can say so). It
  appends no event and changes no memory row; the detail response has `practice_queued`. Chosen over
  "marking the item due" because that would have needed a synthetic event or a projection edit.
- `timezone` is validated against the IANA database (422 otherwise), defaults to `UTC` for new and
  older learners, and is part of `GET/PUT /api/settings` and the learner's `settings`. The `tzdata`
  package is now a backend dependency (the slim Docker image has no system zoneinfo).

**Storage**
- Migration `0009` adds `ix_learning_events_learner_ts`, `ix_learning_events_exercise_id`,
  `ix_attempts_submitted_at`, `ix_attempts_exercise_id` and `ix_evaluations_attempt_id` (the
  attempts/evaluations joins of the history had no index); the models declare them, so the
  autogenerate-diff test stays green.
- `corpus._matches_query` became the public `corpus.matches_query` (reused by the item search);
  `tests/test_api_flow.py::test_learner_setup` now expects `timezone` in the settings.
- `ARCHITECTURE.md` (§4.5/§5/§13) was not touched by the backend work; it still needs an M8
  entry.
