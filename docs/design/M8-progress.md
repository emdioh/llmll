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
