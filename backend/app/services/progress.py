"""Progress review: queries over the event log and existing tables (design: docs/design/M8).

No new source of truth: everything is derived from `learning_events`, `item_memory`,
`learner_items`, attempts/evaluations/contests and reading sessions, and aggregated by the pure
functions of `app.domain.progress`. Voided events are excluded everywhere; placement events
(`context = 'placement'`) count for item state but not for streaks or activity.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.domain import progress as domain
from app.domain.projection import REVIEW_KINDS
from app.domain.selection import MemoryView
from app.services import corpus, queue, stats
from app.services.common import PLACEMENT_PREFIX, SessionError
from app.services.corpus import item_label
from app.services.drills import DRILL_PREFIX, drill_title
from app.services.learner import projection_config, settings_of
from app.store.events import to_event_data
from app.store.models import (
    Attempt,
    Contest,
    Evaluation,
    Exercise,
    Item,
    ItemMemory,
    Learner,
    LearnerItem,
    LearningEvent,
    ReadingSession,
    RemediationItem,
    SourceText,
    StoredExplanation,
    TextVersion,
)

READING_PREFIX = "reading-"
FLASHCARD_TYPES = ("flashcard_recognition", "flashcard_production")
INTRO_TYPES = ("flashcard_intro", "grammar_intro")
GRAMMAR_KINDS = ("grammar", "construction")
KNOWN_STATUSES = ("introduced", "presumed_known")
PRACTICED_KINDS = ("introduce", *REVIEW_KINDS)
RECENT_ANSWERS = 10
TOP_TAGS = 3
MIN_EVIDENCE = 2.0  # `weakest`/`strongest` ignore items with less evidence (n_eff)
TRAJECTORY_MAX_POINTS = 500
MAX_ACTIVITY_DAYS = 366
MAX_FORECAST_DAYS = 60
ITEM_SORTS = ("weakest", "strongest", "recent", "due", "errors")
OVERALL_TO_OUTCOME = {
    "correct": "correct",
    "minor_errors": "assisted",
    "major_errors": "error",
    "off_task": "error",
}


# --- time --------------------------------------------------------------------------------------


def learner_tz(learner: Learner) -> ZoneInfo:
    try:
        return ZoneInfo(settings_of(learner).timezone)
    except (KeyError, ValueError, OSError):  # unknown zone stored by hand: fall back to UTC
        return ZoneInfo("UTC")


def _day_start(day: date, tz: ZoneInfo) -> datetime:
    """UTC instant of local midnight at the start of `day`."""
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


# --- activity ----------------------------------------------------------------------------------


def _attempt_rows(
    db: Session, learner: Learner, since: datetime | None = None, until: datetime | None = None
) -> Iterable[Any]:
    stmt = (
        select(Attempt.submitted_at, Attempt.duration_ms, Exercise.type)
        .join(Exercise, Exercise.id == Attempt.exercise_id)
        .where(
            Exercise.learner_id == learner.id,
            ~Exercise.session_id.startswith(PLACEMENT_PREFIX),
        )
    )
    if since is not None:
        stmt = stmt.where(Attempt.submitted_at >= since)
    if until is not None:
        stmt = stmt.where(Attempt.submitted_at < until)
    return db.execute(stmt).all()


def _finished_readings(
    db: Session, learner: Learner, since: datetime | None = None, until: datetime | None = None
) -> Iterable[Any]:
    stmt = (
        select(ReadingSession.started_at, ReadingSession.finished_at)
        .join(TextVersion, TextVersion.id == ReadingSession.text_version_id)
        .join(SourceText, SourceText.id == TextVersion.text_id)
        .where(SourceText.learner_id == learner.id, ReadingSession.finished_at.is_not(None))
    )
    if since is not None:
        stmt = stmt.where(ReadingSession.finished_at >= since)
    if until is not None:
        stmt = stmt.where(ReadingSession.finished_at < until)
    return db.execute(stmt).all()


def _activity_events(
    db: Session, learner: Learner, since: datetime, until: datetime
) -> list[domain.ActivityEvent]:
    events: list[domain.ActivityEvent] = []
    kinds = {t: domain.REVIEW for t in FLASHCARD_TYPES} | {t: domain.INTRO for t in INTRO_TYPES}
    for submitted_at, duration_ms, type_ in _attempt_rows(db, learner, since, until):
        events.append(
            domain.ActivityEvent(
                submitted_at,
                kinds.get(type_, domain.EXERCISE),
                (duration_ms or 0) / 60000.0,
            )
        )
    for started_at, finished_at in _finished_readings(db, learner, since, until):
        events.append(
            domain.ActivityEvent(
                finished_at, domain.READING, domain.reading_minutes(started_at, finished_at)
            )
        )
    for (introduced_at,) in db.execute(
        select(LearnerItem.introduced_at).where(
            LearnerItem.learner_id == learner.id,
            LearnerItem.introduced_at >= since,
            LearnerItem.introduced_at < until,
        )
    ):
        events.append(domain.ActivityEvent(introduced_at, domain.NEW_ITEM))
    return events


def _all_study_timestamps(db: Session, learner: Learner) -> list[datetime]:
    return [r[0] for r in _attempt_rows(db, learner)] + [
        r[1] for r in _finished_readings(db, learner)
    ]


def _activity_window(
    db: Session, learner: Learner, tz: ZoneInfo, today: date, days: int
) -> list[domain.DayActivity]:
    start = today - timedelta(days=days - 1)
    events = _activity_events(
        db, learner, _day_start(start, tz), _day_start(today + timedelta(days=1), tz)
    )
    return domain.activity_by_day(events, tz, start, today)


def activity(db: Session, learner: Learner, now: datetime, days: int) -> dict[str, Any]:
    tz = learner_tz(learner)
    today = domain.local_date(now, tz)
    start = today - timedelta(days=days - 1)
    rows = _activity_window(db, learner, tz, today, days)
    outcome_rows = db.execute(
        select(LearningEvent.ts, LearningEvent.outcome, Exercise.type)
        .join(Exercise, Exercise.id == LearningEvent.exercise_id)
        .where(
            LearningEvent.learner_id == learner.id,
            LearningEvent.kind == "review",
            LearningEvent.voided_by.is_(None),
            LearningEvent.context.is_(None),
            LearningEvent.outcome.is_not(None),
            LearningEvent.ts >= _day_start(domain.week_start_of(start), tz),
            LearningEvent.ts < _day_start(today + timedelta(days=1), tz),
        )
    ).all()
    weeks = domain.weekly_accuracy(
        [
            domain.ReviewOutcome(ts, "production" if type_ == "production" else "flashcard", o)
            for ts, o, type_ in outcome_rows
        ],
        tz,
        start,
        today,
    )
    return {
        "timezone": tz.key,
        "today": today,
        "days": [_day_out(r) for r in rows],
        "weeks": [
            {
                "week_start": w.week_start,
                "flashcards_correct_rate": w.flashcards_correct_rate,
                "production_correct_rate": w.production_correct_rate,
                "flashcards_n": w.flashcards_n,
                "production_n": w.production_n,
                "n": w.n,
            }
            for w in weeks
        ],
    }


def _day_out(row: domain.DayActivity) -> dict[str, Any]:
    return {
        "date": row.date,
        "reviews": row.reviews,
        "exercises": row.exercises,
        "new_items": row.new_items,
        "readings": row.readings,
        "minutes": row.minutes,
        "active": row.active,
    }


# --- item state --------------------------------------------------------------------------------


def _facet_memories(db: Session, learner: Learner) -> dict[str, list[domain.FacetMemory]]:
    result: dict[str, list[domain.FacetMemory]] = defaultdict(list)
    for row in db.execute(
        select(
            ItemMemory.item_id,
            ItemMemory.facet,
            ItemMemory.stability,
            ItemMemory.mastery,
            ItemMemory.n_effective,
            ItemMemory.due,
        )
        .join(Item, Item.id == ItemMemory.item_id)
        .where(ItemMemory.learner_id == learner.id, ~Item.suspended)
        .order_by(ItemMemory.item_id, ItemMemory.facet)
    ):
        result[row.item_id].append(
            domain.FacetMemory(row.facet, row.stability, row.mastery, row.n_effective, row.due)
        )
    return result


def _statuses(db: Session, learner: Learner) -> dict[str, str]:
    return dict(
        db.execute(
            select(LearnerItem.item_id, LearnerItem.status).where(
                LearnerItem.learner_id == learner.id
            )
        ).all()
    )


def _item_infos(db: Session, kind: str | None = None) -> list[domain.ItemInfo]:
    stmt = select(Item.id, Item.kind, Item.cefr_level).where(~Item.suspended)
    if kind:
        stmt = stmt.where(Item.kind == kind)
    return [domain.ItemInfo(*row) for row in db.execute(stmt)]


def levels(db: Session, learner: Learner) -> list[dict[str, Any]]:
    rows = domain.level_progress(
        _item_infos(db), _statuses(db, learner), _facet_memories(db, learner)
    )
    return [
        {
            "level": r.level,
            "kind": r.kind,
            "total": r.total,
            "introduced": r.introduced,
            "new": r.new,
            "learning": r.learning,
            "young": r.young,
            "mature": r.mature,
            "presumed_known": r.presumed_known,
            "mean_mastery": r.mean_mastery,
        }
        for r in rows
    ]


def forecast(db: Session, learner: Learner, now: datetime, days: int) -> list[dict[str, Any]]:
    memories = [
        MemoryView(item_id=i, facet=f.facet, due=f.due, card=None)
        for i, facets in _facet_memories(db, learner).items()
        for f in facets
    ]
    return domain.due_forecast(memories, now, learner_tz(learner), days)  # type: ignore[return-value]


# --- summary -----------------------------------------------------------------------------------


def _state_counts(
    infos: Sequence[domain.ItemInfo],
    statuses: dict[str, str],
    memories: dict[str, list[domain.FacetMemory]],
) -> dict[str, int]:
    counts = dict.fromkeys(domain.MEMORY_STATES, 0)
    for info in infos:
        counts[domain.item_state(memories.get(info.id, ()), statuses.get(info.id))] += 1
    return counts


def summary(db: Session, learner: Learner, now: datetime) -> dict[str, Any]:
    tz = learner_tz(learner)
    today = domain.local_date(now, tz)
    timestamps = [t for t in _all_study_timestamps(db, learner) if t <= now]
    days = domain.study_days(timestamps, tz)
    streak = domain.streaks(days, today)

    totals = db.execute(
        select(Exercise.type, func.count(), func.coalesce(func.sum(Attempt.duration_ms), 0))
        .join(Attempt, Attempt.exercise_id == Exercise.id)
        .where(
            Exercise.learner_id == learner.id,
            ~Exercise.session_id.startswith(PLACEMENT_PREFIX),
            Attempt.submitted_at <= now,
        )
        .group_by(Exercise.type)
    ).all()
    readings = [r for r in _finished_readings(db, learner, until=now + timedelta(microseconds=1))]
    minutes = sum(int(ms) for _, _, ms in totals) / 60000.0 + sum(
        domain.reading_minutes(s, f) for s, f in readings
    )
    introduced = (
        db.scalar(
            select(func.count())
            .select_from(LearnerItem)
            .where(
                LearnerItem.learner_id == learner.id,
                LearnerItem.introduced_at.is_not(None),
                LearnerItem.introduced_at <= now,
            )
        )
        or 0
    )

    window = _activity_window(db, learner, tz, today, 14)
    this_week = domain.period_totals(window, today - timedelta(days=6), today)
    last_week = domain.period_totals(window, today - timedelta(days=13), today - timedelta(days=7))

    infos = _item_infos(db)
    statuses = _statuses(db, learner)
    memories = _facet_memories(db, learner)
    words = [i for i in infos if i.kind == "lemma"]
    grammar = [i for i in infos if i.kind in GRAMMAR_KINDS]
    end_of_today = _day_start(today + timedelta(days=1), tz)
    due_today = sum(1 for fs in memories.values() for f in fs if f.due and f.due < end_of_today)

    scheduler = stats.learner_stats(db, learner, now)
    return {
        "timezone": tz.key,
        "today": today,
        "streak": {
            "current": streak.current,
            "longest": streak.longest,
            "last_study_day": streak.last_study_day,
            "studied_today": today in days,
        },
        "study_days_total": len(days),
        "totals": {
            "reviews": sum(n for t, n, _ in totals if t in FLASHCARD_TYPES),
            "exercises": sum(n for t, n, _ in totals if t not in (*FLASHCARD_TYPES, *INTRO_TYPES)),
            "readings": len(readings),
            "items_introduced": introduced,
            "minutes": round(minutes, 1),
        },
        "this_week": _period_out(this_week),
        "last_week": _period_out(last_week),
        "retention": {
            "observed": scheduler["observed_retention"],
            "target": scheduler["target_retention"],
            "n_reviews": sum(b["n"] for b in scheduler["calibration"]),
        },
        "states": _state_counts(infos, statuses, memories),
        "states_words": _state_counts(words, statuses, memories),
        "states_grammar": _state_counts(grammar, statuses, memories),
        "due_now": queue.backlog(db, learner.id, now),
        "due_today": due_today,
    }


def _period_out(p: domain.PeriodTotals) -> dict[str, Any]:
    return {
        "study_days": p.study_days,
        "reviews": p.reviews,
        "exercises": p.exercises,
        "readings": p.readings,
        "new_items": p.new_items,
        "minutes": p.minutes,
    }


# --- items -------------------------------------------------------------------------------------


def _facet_out(f: domain.FacetMemory) -> dict[str, Any]:
    return {
        "facet": f.facet,
        "mastery": f.mastery,
        "stability": f.stability,
        "due": f.due,
        "n_eff": f.n_eff,
        "state": domain.memory_state(f),
    }


def _event_stats(db: Session, learner: Learner) -> dict[str, dict[str, Any]]:
    """Per item: counts of graded outcomes and the last time it was practiced (non-voided)."""

    def count(outcome: str) -> Any:
        return func.coalesce(func.sum(case((LearningEvent.outcome == outcome, 1), else_=0)), 0)

    result: dict[str, dict[str, Any]] = {}
    rows = db.execute(
        select(
            LearningEvent.item_id,
            func.max(LearningEvent.ts),
            count("correct"),
            count("assisted"),
            count("error"),
        )
        .where(
            LearningEvent.learner_id == learner.id,
            LearningEvent.voided_by.is_(None),
            LearningEvent.kind.in_(PRACTICED_KINDS),
        )
        .group_by(LearningEvent.item_id)
    )
    for item_id, last, correct, assisted, error in rows:
        result[item_id] = {
            "last_practiced": last,
            "correct": int(correct),
            "assisted": int(assisted),
            "error": int(error),
        }
    return result


def _tag_errors(db: Session, learner: Learner, item_ids: Sequence[str]) -> dict[str, Counter[str]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    if not item_ids:
        return counters
    for item_id, tags in db.execute(
        select(LearningEvent.item_id, LearningEvent.diagnostic_tags).where(
            LearningEvent.learner_id == learner.id,
            LearningEvent.item_id.in_(item_ids),
            LearningEvent.voided_by.is_(None),
            LearningEvent.outcome == "error",
        )
    ):
        counters[item_id].update(tags or [])
    return counters


def _tags_out(counter: Counter[str], limit: int | None = None) -> list[dict[str, Any]]:
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"tag": tag, "count": n} for tag, n in ranked[:limit]]


def _rate(errors: int, total: int) -> float | None:
    return errors / total if total else None


def list_items(
    db: Session,
    learner: Learner,
    *,
    kind: str | None,
    sort: str,
    q: str | None,
    level: str | None,
    state: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    stmt = select(Item).where(~Item.suspended)
    if kind:
        stmt = stmt.where(Item.kind == kind)
    if level:
        stmt = stmt.where(Item.cefr_level == level)
    items = [i for i in db.scalars(stmt) if corpus.matches_query(i, q)]
    statuses = _statuses(db, learner)
    memories = _facet_memories(db, learner)
    events = _event_stats(db, learner)

    rows = []
    for item in items:
        facets = memories.get(item.id, [])
        item_state = domain.item_state(facets, statuses.get(item.id))
        if state and item_state != state:
            continue
        ev = events.get(item.id, {})
        graded = ev.get("correct", 0) + ev.get("assisted", 0) + ev.get("error", 0)
        dues = [f.due for f in facets if f.due is not None]
        rows.append(
            {
                "item": item,
                "state": item_state,
                "facets": facets,
                "mastery": domain.item_mastery(facets),
                "n_eff": sum(f.n_eff for f in facets),
                "due": min(dues) if dues else None,
                "last_practiced": ev.get("last_practiced"),
                "errors": ev.get("error", 0),
                "graded": graded,
            }
        )

    selected = _sorted(rows, sort)
    page = selected[offset : offset + limit]
    tags = _tag_errors(db, learner, [r["item"].id for r in page])
    return {
        "total": len(selected),
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r["item"].id,
                "kind": r["item"].kind,
                "level": r["item"].cefr_level,
                "label": item_label(r["item"]),
                "translation_it": corpus.item_translation_it(r["item"]),
                "status": statuses.get(r["item"].id),
                "state": r["state"],
                "mastery": r["mastery"],
                "n_eff": r["n_eff"],
                "due": r["due"],
                "last_practiced": r["last_practiced"],
                "errors": r["errors"],
                "answers": r["graded"],
                "error_rate": _rate(r["errors"], r["graded"]),
                "top_tags": _tags_out(tags.get(r["item"].id, Counter()), TOP_TAGS),
                "facets": [_facet_out(f) for f in r["facets"]],
            }
            for r in page
        ],
    }


def grammar_progress(db: Session, learner: Learner) -> dict[str, dict[str, Any]]:
    """Learner status of every grammar item, keyed by item id (one pass, no per-item queries)."""
    rows = {
        row.item_id: row
        for row in db.execute(
            select(LearnerItem.item_id, LearnerItem.status, LearnerItem.introduced_at).where(
                LearnerItem.learner_id == learner.id
            )
        )
    }
    memories = _facet_memories(db, learner)
    events = _event_stats(db, learner)
    result: dict[str, dict[str, Any]] = {}
    for (item_id,) in db.execute(select(Item.id).where(Item.kind == "grammar", ~Item.suspended)):
        facets = memories.get(item_id, [])
        row = rows.get(item_id)
        status = row.status if row else None
        dues = [f.due for f in facets if f.due is not None]
        result[item_id] = {
            "status": status,
            "state": domain.item_state(facets, status),
            "mastery": domain.item_mastery(facets),
            "introduced_at": row.introduced_at if row else None,
            "last_practiced": events.get(item_id, {}).get("last_practiced"),
            "due": min(dues) if dues else None,
        }
    return result


def _sorted(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    far = datetime.max.replace(tzinfo=UTC)
    if sort in ("weakest", "strongest"):
        ranked = [r for r in rows if r["mastery"] is not None]
        enough = [r for r in ranked if r["n_eff"] >= MIN_EVIDENCE]
        pool = enough or ranked
        sign = 1 if sort == "weakest" else -1
        return sorted(pool, key=lambda r: (sign * r["mastery"], r["item"].id))
    if sort == "recent":
        practiced = [r for r in rows if r["last_practiced"] is not None]
        return sorted(practiced, key=lambda r: (r["last_practiced"], r["item"].id), reverse=True)
    if sort == "due":
        scheduled = [r for r in rows if r["due"] is not None]
        return sorted(scheduled, key=lambda r: (r["due"] or far, r["item"].id))
    if sort == "errors":
        failed = [r for r in rows if r["errors"] > 0]
        return sorted(failed, key=lambda r: (-r["errors"], r["item"].id))
    raise SessionError(422, f"Unknown sort: {sort}")


# --- item detail -------------------------------------------------------------------------------


def _card_outcome(
    exercise: Exercise, attempt: Attempt, evaluation: Evaluation | None
) -> str | None:
    """The outcome of an answered card as it stands now (after any contest); None for intros."""
    if exercise.type in INTRO_TYPES:
        return None
    if evaluation is None:
        return attempt.outcome
    result = evaluation.result
    if exercise.type in FLASHCARD_TYPES:
        items = result.get("items") or []
        return items[0]["outcome"] if items else attempt.outcome
    outcome = OVERALL_TO_OUTCOME.get(result.get("overall", ""), attempt.outcome)
    return "assisted" if outcome == "correct" and attempt.used_hint else outcome


def _prompt_text(exercise: Exercise) -> str:
    p = exercise.prompt
    if exercise.type == "flashcard_intro":
        return p.get("de", "")
    if exercise.type == "grammar_intro":
        return p.get("title", "")
    if exercise.type == "flashcard_recognition":
        return p.get("de", "")
    if exercise.type == "flashcard_production":
        return p.get("it", "")
    return p.get("prompt") or ""


def _answer_text(exercise: Exercise, attempt: Attempt) -> str | None:
    answer = attempt.answer or {}
    if exercise.type == "flashcard_recognition":
        choice = answer.get("choice")
        options = exercise.prompt.get("options") or []
        return options[choice] if isinstance(choice, int) and 0 <= choice < len(options) else None
    if exercise.type in INTRO_TYPES:
        return None
    return answer.get("text")


def _expected_text(exercise: Exercise, evaluation: Evaluation | None) -> str | None:
    if exercise.type in INTRO_TYPES:
        return None
    if exercise.type == "flashcard_recognition":
        return exercise.solution.get("translation_it")
    if exercise.type == "flashcard_production":
        return exercise.solution.get("text")
    return evaluation.result.get("corrected_sentence") if evaluation else None


def _load_card_context(db: Session, attempt_ids: Sequence[int]) -> dict[str, Any]:
    """Latest evaluation and latest contest per attempt, and labels of the items involved."""
    evaluations: dict[int, Evaluation] = {}
    evaluation_owner: dict[int, int] = {}
    if attempt_ids:
        for ev in db.scalars(
            select(Evaluation).where(Evaluation.attempt_id.in_(attempt_ids)).order_by(Evaluation.id)
        ):
            evaluations[ev.attempt_id] = ev  # ascending ids: the last one wins
            evaluation_owner[ev.id] = ev.attempt_id
    contests: dict[int, Contest] = {}
    if evaluation_owner:
        for contest in db.scalars(
            select(Contest)
            .where(Contest.evaluation_id.in_(list(evaluation_owner)))
            .order_by(Contest.id)
        ):
            contests[evaluation_owner[contest.evaluation_id]] = contest
    return {"evaluations": evaluations, "contests": contests}


def _labels(db: Session, item_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted(set(item_ids))
    if not ids:
        return {}
    return {i.id: item_label(i) for i in db.scalars(select(Item).where(Item.id.in_(ids)))}


def _cards(
    db: Session, pairs: Sequence[tuple[Exercise, Attempt]], item_id: str | None = None
) -> list[dict[str, Any]]:
    """Answer cards for (exercise, attempt) pairs, in the given order."""
    context = _load_card_context(db, [a.id for _, a in pairs])
    wanted: set[str] = set()
    for exercise, attempt in pairs:
        wanted.update(t["item_id"] for t in exercise.targets)
        ev = context["evaluations"].get(attempt.id)
        if ev is not None:
            wanted.update(i["item_id"] for i in ev.result.get("items", []))
            wanted.update(e["item_id"] for e in ev.result.get("errors", []) if e.get("item_id"))
    labels = _labels(db, wanted)
    cards = []
    for exercise, attempt in pairs:
        ev: Evaluation | None = context["evaluations"].get(attempt.id)
        contest: Contest | None = context["contests"].get(attempt.id)
        result = ev.result if ev is not None else {}
        errors = [
            {**e, "label": labels.get(e.get("item_id") or "", e.get("item_id"))}
            for e in result.get("errors", [])
        ]
        items = [
            {
                "item_id": i["item_id"],
                "label": labels.get(i["item_id"], i["item_id"]),
                "outcome": i["outcome"],
                "diagnostic_tags": list(i.get("diagnostic_tags") or []),
            }
            for i in result.get("items", [])
        ]
        card = {
            "exercise_id": exercise.id,
            "attempt_id": attempt.id,
            "evaluation_id": ev.id if ev is not None else None,
            "type": exercise.type,
            "subtype": exercise.prompt.get("subtype") if exercise.type == "production" else None,
            "answered_at": attempt.submitted_at,
            "outcome": _card_outcome(exercise, attempt, ev),
            "prompt": _prompt_text(exercise),
            "instructions": exercise.prompt.get("instructions"),
            "options": exercise.prompt.get("options"),
            "answer": _answer_text(exercise, attempt),
            "expected": _expected_text(exercise, ev),
            "feedback": result.get("feedback") or None,
            "used_hint": attempt.used_hint,
            "duration_ms": attempt.duration_ms,
            "errors": errors,
            "items": items,
            "contest": (
                {
                    "id": contest.id,
                    "status": contest.status,
                    "verdict": contest.verdict,
                    "reason": contest.reason,
                    "rationale": contest.rationale,
                    "created_at": contest.created_at,
                }
                if contest is not None
                else None
            ),
        }
        if item_id is not None:
            own = next((i for i in items if i["item_id"] == item_id), None)
            card["item_outcome"] = own["outcome"] if own else None
            card["item_errors"] = [e for e in errors if e.get("item_id") == item_id]
        cards.append(card)
    return cards


def item_detail(db: Session, learner: Learner, item_id: str) -> dict[str, Any]:
    detail = corpus.get_item(db, learner.id, item_id)
    if detail is None:
        raise SessionError(404, "Item not found")
    cfg = projection_config(settings_of(learner))
    events = db.scalars(
        select(LearningEvent)
        .where(
            LearningEvent.learner_id == learner.id,
            LearningEvent.item_id == item_id,
            LearningEvent.voided_by.is_(None),
        )
        .order_by(LearningEvent.ts, LearningEvent.id)
    ).all()
    memories = {
        row.facet: domain.FacetMemory(
            row.facet, row.stability, row.mastery, row.n_effective, row.due
        )
        for row in db.scalars(
            select(ItemMemory).where(
                ItemMemory.learner_id == learner.id, ItemMemory.item_id == item_id
            )
        )
    }

    by_facet: dict[str, list[LearningEvent]] = defaultdict(list)
    for event in events:
        by_facet[event.facet].append(event)
    tag_counts: dict[str, Counter[str]] = defaultdict(Counter)
    outcome_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        if event.kind in REVIEW_KINDS and event.outcome:
            outcome_counts[event.facet][event.outcome] += 1
            if event.outcome == "error":
                tag_counts[event.facet].update(event.diagnostic_tags or [])

    facets = []
    for facet in sorted(set(by_facet) | set(memories)):
        points = domain.mastery_trajectory(
            [to_event_data(e) for e in by_facet.get(facet, [])], cfg, item_id, facet
        )
        memory = memories.get(facet)
        counts = outcome_counts[facet]
        facets.append(
            {
                **(_facet_out(memory) if memory else {"facet": facet, "state": "new"}),
                "counts": {k: counts.get(k, 0) for k in ("correct", "assisted", "error")},
                "tag_errors": _tags_out(tag_counts[facet]),
                "trajectory_total": len(points),
                "trajectory": [
                    {
                        "event_id": p.event_id,
                        "ts": p.ts,
                        "kind": p.kind,
                        "outcome": p.outcome,
                        "mastery": p.mastery,
                        "stability": p.stability,
                    }
                    for p in points[-TRAJECTORY_MAX_POINTS:]
                ],
            }
        )

    all_tags: Counter[str] = Counter()
    for counter in tag_counts.values():
        all_tags.update(counter)
    totals: Counter[str] = Counter()
    for counter in outcome_counts.values():
        totals.update(counter)

    status = detail["status"]
    now_facets = list(memories.values())
    return {
        "item": detail,
        "state": domain.item_state(now_facets, status),
        "mastery": domain.item_mastery(now_facets),
        "counts": {k: totals.get(k, 0) for k in ("correct", "assisted", "error")},
        "tag_errors": _tags_out(all_tags),
        "facets": facets,
        "recent_answers": _recent_answers(db, learner, item_id),
        "explanation": _cached_explanation(db, item_id),
        "practice_queued": _queued_practice(db, learner, item_id) is not None,
    }


def _recent_answers(db: Session, learner: Learner, item_id: str) -> list[dict[str, Any]]:
    attempt_ids = []
    for (attempt_id,) in db.execute(
        select(LearningEvent.attempt_id)
        .where(
            LearningEvent.learner_id == learner.id,
            LearningEvent.item_id == item_id,
            LearningEvent.voided_by.is_(None),
            LearningEvent.kind.in_(("review", "implicit")),
            LearningEvent.attempt_id.is_not(None),
        )
        .order_by(LearningEvent.ts.desc(), LearningEvent.id.desc())
        .limit(RECENT_ANSWERS * 3)
    ):
        if attempt_id not in attempt_ids:
            attempt_ids.append(attempt_id)
    attempt_ids = attempt_ids[:RECENT_ANSWERS]
    if not attempt_ids:
        return []
    pairs = {
        attempt.id: (exercise, attempt)
        for attempt, exercise in db.execute(
            select(Attempt, Exercise)
            .join(Exercise, Exercise.id == Attempt.exercise_id)
            .where(Attempt.id.in_(attempt_ids))
        )
    }
    return _cards(db, [pairs[i] for i in attempt_ids if i in pairs], item_id=item_id)


def _cached_explanation(db: Session, item_id: str) -> dict[str, Any] | None:
    row = db.scalar(
        select(StoredExplanation)
        .where(StoredExplanation.item_id == item_id)
        .order_by(StoredExplanation.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "evaluation_id": row.evaluation_id,
        "markdown": row.markdown,
        "examples": row.examples,
        "created_at": row.created_at,
    }


def _queued_practice(db: Session, learner: Learner, item_id: str) -> RemediationItem | None:
    return db.scalar(
        select(RemediationItem)
        .where(
            RemediationItem.learner_id == learner.id,
            RemediationItem.item_id == item_id,
            RemediationItem.consumed_at.is_(None),
        )
        .limit(1)
    )


def practice_item(db: Session, learner: Learner, item_id: str, now: datetime) -> dict[str, Any]:
    """Put an item in the remediation queue: the next session's production exercise targets it.

    A queue entry is not memory state (it changes no `item_memory` row and appends no event), so
    this does not break the event-sourcing rule. Idempotent while an entry is pending.
    """
    item = db.get(Item, item_id)
    if item is None or item.suspended:
        raise SessionError(404, "Item not found")
    row = db.get(LearnerItem, (learner.id, item_id))
    if row is None or row.status not in KNOWN_STATUSES:
        raise SessionError(409, "Only items you have met can be practiced")
    already = _queued_practice(db, learner, item_id) is not None
    if not already:
        db.add(
            RemediationItem(
                learner_id=learner.id,
                item_id=item_id,
                diagnostic_tags=[],
                evaluation_id=None,
                created_at=now,
            )
        )
        db.commit()
    return {
        "item_id": item_id,
        "queued": True,
        "already_queued": already,
        "production_slots": settings_of(learner).production_slots,
    }


# --- history -----------------------------------------------------------------------------------


def _session_stats(db: Session, session_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Cards answered, correct rate, new items and duration for review/reading-summary sessions."""
    if not session_ids:
        return {}
    rows = db.execute(
        select(Exercise, Attempt)
        .join(Attempt, Attempt.exercise_id == Exercise.id)
        .where(Exercise.session_id.in_(session_ids))
        .order_by(Attempt.submitted_at, Attempt.id)
    ).all()
    context = _load_card_context(db, [a.id for _, a in rows])
    acc: dict[str, dict[str, Any]] = {
        s: {"answered": 0, "graded": 0, "correct": 0, "ms": None, "first": None, "last": None}
        for s in session_ids
    }
    for exercise, attempt in rows:
        entry = acc[exercise.session_id]
        entry["answered"] += 1
        outcome = _card_outcome(exercise, attempt, context["evaluations"].get(attempt.id))
        if outcome is not None:
            entry["graded"] += 1
            entry["correct"] += outcome == "correct"
        if attempt.duration_ms is not None:
            entry["ms"] = (entry["ms"] or 0) + attempt.duration_ms
        entry["first"] = entry["first"] or attempt.submitted_at
        entry["last"] = attempt.submitted_at
    new_items = dict(
        db.execute(
            select(Exercise.session_id, func.count(distinct(LearningEvent.item_id)))
            .join(Exercise, Exercise.id == LearningEvent.exercise_id)
            .where(
                Exercise.session_id.in_(session_ids),
                LearningEvent.kind == "introduce",
                LearningEvent.voided_by.is_(None),
            )
            .group_by(Exercise.session_id)
        ).all()
    )
    for session_id, entry in acc.items():
        entry["new_items"] = new_items.get(session_id, 0)
        entry["correct_rate"] = entry["correct"] / entry["graded"] if entry["graded"] else None
    return acc


def _session_minutes(entry: dict[str, Any]) -> float | None:
    if entry["ms"] is not None:
        return round(entry["ms"] / 60000.0, 1)
    if entry["first"] is not None and entry["last"] is not None:
        return round(domain.reading_minutes(entry["first"], entry["last"]), 1)
    return None


def _review_session_filter() -> Any:
    return (
        ~Exercise.session_id.startswith(PLACEMENT_PREFIX),
        ~Exercise.session_id.startswith(READING_PREFIX),
    )


def history(db: Session, learner: Learner, limit: int, offset: int) -> dict[str, Any]:
    """Review sessions and finished readings, newest first."""
    window = limit + offset
    session_rows = db.execute(
        select(
            Exercise.session_id,
            func.min(Attempt.submitted_at),
            func.max(Attempt.submitted_at),
        )
        .join(Attempt, Attempt.exercise_id == Exercise.id)
        .where(Exercise.learner_id == learner.id, *_review_session_filter())
        .group_by(Exercise.session_id)
        .order_by(func.max(Attempt.submitted_at).desc(), Exercise.session_id)
        .limit(window)
    ).all()
    reading_rows = db.execute(
        select(ReadingSession, TextVersion)
        .join(TextVersion, TextVersion.id == ReadingSession.text_version_id)
        .join(SourceText, SourceText.id == TextVersion.text_id)
        .where(SourceText.learner_id == learner.id, ReadingSession.finished_at.is_not(None))
        .order_by(ReadingSession.finished_at.desc(), ReadingSession.id.desc())
        .limit(window)
    ).all()
    total = (
        db.scalar(
            select(func.count(distinct(Exercise.session_id)))
            .join(Attempt, Attempt.exercise_id == Exercise.id)
            .where(Exercise.learner_id == learner.id, *_review_session_filter())
        )
        or 0
    ) + (
        db.scalar(
            select(func.count())
            .select_from(ReadingSession)
            .join(TextVersion, TextVersion.id == ReadingSession.text_version_id)
            .join(SourceText, SourceText.id == TextVersion.text_id)
            .where(SourceText.learner_id == learner.id, ReadingSession.finished_at.is_not(None))
        )
        or 0
    )

    merged: list[tuple[datetime, str, Any]] = [
        (last, f"s:{sid}", ("session", sid, first, last)) for sid, first, last in session_rows
    ] + [(r.finished_at, f"r:{r.id:010d}", ("reading", r, v)) for r, v in reading_rows]
    merged.sort(key=lambda m: (m[0], m[1]), reverse=True)
    page = merged[offset : offset + limit]

    stats_ids = [m[2][1] for m in page if m[2][0] == "session"] + [
        f"{READING_PREFIX}{m[2][1].id}" for m in page if m[2][0] == "reading"
    ]
    session_stats = _session_stats(db, stats_ids)
    rows = []
    for _, _, payload in page:
        if payload[0] == "session":
            _, sid, first, last = payload
            entry = session_stats[sid]
            rows.append(
                {
                    "kind": "session",
                    "id": sid,
                    "started_at": first,
                    "ended_at": last,
                    "duration_minutes": _session_minutes(entry),
                    "cards_answered": entry["answered"],
                    "correct_rate": entry["correct_rate"],
                    "new_items": entry["new_items"],
                    "title": _session_title(db, sid),
                    "words_looked_up": None,
                }
            )
        else:
            _, reading, version = payload
            entry = session_stats[f"{READING_PREFIX}{reading.id}"]
            rows.append(
                {
                    "kind": "reading",
                    "id": str(reading.id),
                    "started_at": reading.started_at,
                    "ended_at": reading.finished_at,
                    "duration_minutes": round(
                        domain.reading_minutes(reading.started_at, reading.finished_at), 1
                    ),
                    "cards_answered": entry["answered"],
                    "correct_rate": entry["correct_rate"],
                    "new_items": entry["new_items"],
                    "title": version.title,
                    "words_looked_up": len({lk.get("lemma") for lk in reading.lookups}),
                }
            )
    return {"total": total, "limit": limit, "offset": offset, "items": rows}


def _session_title(db: Session, session_id: str) -> str | None:
    """Drills are titled after their grammar point; review sessions have no title."""
    if not session_id.startswith(DRILL_PREFIX):
        return None
    first = db.scalar(
        select(Exercise)
        .where(Exercise.session_id == session_id)
        .order_by(Exercise.created_at, Exercise.id)
        .limit(1)
    )
    if first is None or not first.targets:
        return None
    return drill_title(db.get(Item, first.targets[0]["item_id"]))


def session_detail(db: Session, learner: Learner, session_id: str) -> dict[str, Any]:
    if session_id.startswith((PLACEMENT_PREFIX, READING_PREFIX)):
        raise SessionError(404, "Session not found")
    pairs = _answered_pairs(db, learner, session_id)
    if not pairs:
        raise SessionError(404, "Session not found")
    entry = _session_stats(db, [session_id])[session_id]
    return {
        "kind": "session",
        "id": session_id,
        "started_at": entry["first"],
        "ended_at": entry["last"],
        "duration_minutes": _session_minutes(entry),
        "cards_answered": entry["answered"],
        "correct_rate": entry["correct_rate"],
        "new_items": entry["new_items"],
        "cards": _cards(db, pairs),
    }


def _answered_pairs(
    db: Session, learner: Learner, session_id: str
) -> list[tuple[Exercise, Attempt]]:
    return [
        (exercise, attempt)
        for exercise, attempt in db.execute(
            select(Exercise, Attempt)
            .join(Attempt, Attempt.exercise_id == Exercise.id)
            .where(Exercise.learner_id == learner.id, Exercise.session_id == session_id)
            .order_by(Attempt.submitted_at, Attempt.id)
        )
    ]


def reading_detail(db: Session, learner: Learner, reading_id: int) -> dict[str, Any]:
    found = db.execute(
        select(ReadingSession, TextVersion, SourceText)
        .join(TextVersion, TextVersion.id == ReadingSession.text_version_id)
        .join(SourceText, SourceText.id == TextVersion.text_id)
        .where(
            ReadingSession.id == reading_id,
            SourceText.learner_id == learner.id,
            ReadingSession.finished_at.is_not(None),
        )
    ).first()
    if found is None:
        raise SessionError(404, "Reading session not found")
    reading, version, text = found
    tokens = {t["i"]: t for t in version.analysis.get("tokens", [])}
    labels = _labels(db, [lk["item_id"] for lk in reading.lookups if lk.get("item_id")])
    lookups = []
    for lk in reading.lookups:
        token = tokens.get(lk.get("token_index"))
        lookups.append(
            {
                "token_index": lk.get("token_index"),
                "word": version.body[token["start"] : token["end"]] if token else lk.get("lemma"),
                "lemma": lk.get("lemma"),
                "item_id": lk.get("item_id"),
                "label": labels.get(lk.get("item_id") or ""),
            }
        )
    pairs = _answered_pairs(db, learner, f"{READING_PREFIX}{reading_id}")
    cards = _cards(db, pairs)
    return {
        "kind": "reading",
        "id": str(reading.id),
        "text_id": text.id,
        "title": version.title,
        "level": version.level,
        "body": version.body,
        "source_title": text.source_title,
        "source_url": text.source_url,
        "started_at": reading.started_at,
        "ended_at": reading.finished_at,
        "duration_minutes": round(
            domain.reading_minutes(reading.started_at, reading.finished_at), 1
        ),
        "lookups": lookups,
        "summary": cards[0] if cards else None,
    }
