"""Progress review: study days, streaks, activity, memory states, forecast (design: M8). Pure.

Everything here is derived from rows that the caller has already selected (voided events and
placement activity are filtered out by the caller); no database, network or clock access.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.curriculum.schema import CEFR_LEVELS
from app.domain.config import ProjectionConfig
from app.domain.projection import EventData, MemoryState, apply
from app.domain.selection import MemoryView

LEARNING_MAX_STABILITY_DAYS = 1.0  # stability below this: "learning"
YOUNG_MAX_STABILITY_DAYS = 21.0  # below this: "young"; from here on: "mature"
READING_MINUTES_CAP = 60.0
MEMORY_STATES = ("new", "learning", "young", "mature", "presumed_known")

# Activity kinds (see `ActivityEvent`).
REVIEW, EXERCISE, INTRO, READING, NEW_ITEM = "review", "exercise", "intro", "reading", "new_item"


def local_date(ts: datetime, tz: ZoneInfo) -> date:
    """The calendar day of `ts` in the learner's time zone (naive timestamps are UTC)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    return ts.astimezone(tz).date()


# --- study days and streaks --------------------------------------------------------------------


def study_days(timestamps: Iterable[datetime], tz: ZoneInfo) -> set[date]:
    """Local calendar days with at least one learning activity."""
    return {local_date(ts, tz) for ts in timestamps}


@dataclass(frozen=True)
class Streaks:
    current: int
    longest: int
    last_study_day: date | None


def streaks(days: set[date], today: date) -> Streaks:
    """Current and longest run of consecutive study days.

    The current streak counts back from today when today is a study day, else from yesterday: a
    day without study does not break it until the day is over. Days after `today` are ignored.
    """
    past = sorted(d for d in days if d <= today)
    if not past:
        return Streaks(0, 0, None)
    longest = run = 1
    for before, after in zip(past, past[1:], strict=False):
        run = run + 1 if after - before == timedelta(days=1) else 1
        longest = max(longest, run)
    anchor = today if today in days else today - timedelta(days=1)
    current = 0
    day = anchor
    while day in days:
        current += 1
        day -= timedelta(days=1)
    return Streaks(current, longest, past[-1])


# --- activity ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityEvent:
    """One thing that happened: an answered card, a finished reading or a newly introduced item.

    kind: `review` (flashcard), `exercise` (written exercise), `intro` (intro card: counts as
    study but not as review), `reading` (finished reading session) or `new_item`.
    """

    ts: datetime
    kind: str
    minutes: float = 0.0


@dataclass(frozen=True)
class DayActivity:
    date: date
    reviews: int = 0
    exercises: int = 0
    new_items: int = 0
    readings: int = 0
    minutes: float = 0.0
    active: bool = False  # a study day: an answered card/exercise or a finished reading


def reading_minutes(started_at: datetime, finished_at: datetime) -> float:
    """Duration of a reading session in minutes, capped (a forgotten open tab is not study)."""
    return min(max((finished_at - started_at).total_seconds() / 60.0, 0.0), READING_MINUTES_CAP)


def activity_by_day(
    events: Iterable[ActivityEvent], tz: ZoneInfo, start: date, end: date
) -> list[DayActivity]:
    """One row per local day from `start` to `end` inclusive (empty days included)."""
    rows = {
        start + timedelta(days=n): DayActivity(start + timedelta(days=n))
        for n in range((end - start).days + 1)
    }
    for event in events:
        day = local_date(event.ts, tz)
        row = rows.get(day)
        if row is None:
            continue
        if event.kind == REVIEW:
            row = replace(row, reviews=row.reviews + 1, active=True)
        elif event.kind == EXERCISE:
            row = replace(row, exercises=row.exercises + 1, active=True)
        elif event.kind == INTRO:
            row = replace(row, active=True)
        elif event.kind == READING:
            row = replace(row, readings=row.readings + 1, active=True)
        elif event.kind == NEW_ITEM:
            row = replace(row, new_items=row.new_items + 1)
        row = replace(row, minutes=row.minutes + event.minutes)
        rows[day] = row
    return [replace(r, minutes=round(r.minutes, 1)) for r in rows.values()]


@dataclass(frozen=True)
class PeriodTotals:
    study_days: int
    reviews: int
    exercises: int
    readings: int
    new_items: int
    minutes: float


def period_totals(days: Iterable[DayActivity], start: date, end: date) -> PeriodTotals:
    """Sum of the day rows from `start` to `end` inclusive."""
    chosen = [d for d in days if start <= d.date <= end]
    return PeriodTotals(
        study_days=sum(1 for d in chosen if d.active),
        reviews=sum(d.reviews for d in chosen),
        exercises=sum(d.exercises for d in chosen),
        readings=sum(d.readings for d in chosen),
        new_items=sum(d.new_items for d in chosen),
        minutes=round(sum(d.minutes for d in chosen), 1),
    )


@dataclass(frozen=True)
class ReviewOutcome:
    """A non-voided review event, reduced to what the accuracy curve needs."""

    ts: datetime
    source: str  # "flashcard" | "production"
    outcome: str  # correct | assisted | error


@dataclass(frozen=True)
class WeekAccuracy:
    week_start: date  # Monday
    flashcards_correct_rate: float | None
    production_correct_rate: float | None
    flashcards_n: int
    production_n: int
    n: int


def week_start_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly_accuracy(
    outcomes: Iterable[ReviewOutcome], tz: ZoneInfo, first_week: date, last_week: date
) -> list[WeekAccuracy]:
    """Share of `correct` outcomes per Monday-based local week, for flashcards and production.

    `first_week`/`last_week` are any days inside the first/last week wanted (empty weeks give
    rows with null rates).
    """
    counts: dict[date, dict[str, list[int]]] = defaultdict(
        lambda: {"flashcard": [0, 0], "production": [0, 0]}
    )
    for item in outcomes:
        bucket = counts[week_start_of(local_date(item.ts, tz))].get(item.source)
        if bucket is None:
            continue
        bucket[0] += 1
        bucket[1] += item.outcome == "correct"
    result = []
    week = week_start_of(first_week)
    while week <= week_start_of(last_week):
        flash_n, flash_ok = counts[week]["flashcard"] if week in counts else (0, 0)
        prod_n, prod_ok = counts[week]["production"] if week in counts else (0, 0)
        result.append(
            WeekAccuracy(
                week_start=week,
                flashcards_correct_rate=flash_ok / flash_n if flash_n else None,
                production_correct_rate=prod_ok / prod_n if prod_n else None,
                flashcards_n=flash_n,
                production_n=prod_n,
                n=flash_n + prod_n,
            )
        )
        week += timedelta(days=7)
    return result


# --- memory states -----------------------------------------------------------------------------


@dataclass(frozen=True)
class FacetMemory:
    """The projected state of one (item, facet), reduced to what progress views need."""

    facet: str
    stability: float | None  # days; None while the facet has no FSRS card
    mastery: float
    n_eff: float = 0.0
    due: datetime | None = None


def memory_state(memory: FacetMemory | None, presumed_known: bool = False) -> str:
    """`new | learning | young | mature | presumed_known` of one facet.

    learning = stability below 1 day, young below 21 days, mature from 21 days. A facet without
    a card is `presumed_known` when the learner declared it known (no events yet), else `new`.
    """
    if memory is None or memory.stability is None:
        return "presumed_known" if presumed_known else "new"
    if memory.stability < LEARNING_MAX_STABILITY_DAYS:
        return "learning"
    if memory.stability < YOUNG_MAX_STABILITY_DAYS:
        return "young"
    return "mature"


def item_state(facets: Sequence[FacetMemory], status: str | None) -> str:
    """State of an item: that of its least stable facet (an item is mature only when all are)."""
    carded = [f for f in facets if f.stability is not None]
    if not carded:
        return memory_state(None, presumed_known=status == "presumed_known")
    return memory_state(min(carded, key=lambda f: f.stability or 0.0))


def item_mastery(facets: Sequence[FacetMemory]) -> float | None:
    """Mean mastery over the facets; None for an item without memory."""
    return sum(f.mastery for f in facets) / len(facets) if facets else None


def due_forecast(
    memories: Iterable[MemoryView], now: datetime, tz: ZoneInfo, days: int
) -> list[dict[str, date | int]]:
    """Memories due per local day for `days` days from today; overdue ones count on day 0."""
    today = local_date(now, tz)
    counts = [0] * days
    for memory in memories:
        if memory.due is None:
            continue
        index = max((local_date(memory.due, tz) - today).days, 0)
        if index < days:
            counts[index] += 1
    return [{"date": today + timedelta(days=n), "due": c} for n, c in enumerate(counts)]


# --- level progress ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemInfo:
    id: str
    kind: str  # lemma | grammar | construction
    level: str


@dataclass(frozen=True)
class LevelProgress:
    level: str
    kind: str
    total: int
    introduced: int  # learner status "introduced"
    new: int  # not met yet (and not presumed known)
    learning: int
    young: int
    mature: int
    presumed_known: int
    mean_mastery: float | None  # over items with a card


def level_progress(
    items: Iterable[ItemInfo],
    learner_items: Mapping[str, str],
    memories: Mapping[str, Sequence[FacetMemory]],
) -> list[LevelProgress]:
    """Per CEFR level and kind: how many items are in each memory state.

    `learner_items` maps item id to learner status; `memories` maps item id to its facets.
    """
    groups: dict[tuple[str, str], list[ItemInfo]] = defaultdict(list)
    for item in items:
        groups[(item.level, item.kind)].append(item)

    def order(key: tuple[str, str]) -> tuple[int, str]:
        level, kind = key
        return (CEFR_LEVELS.index(level) if level in CEFR_LEVELS else len(CEFR_LEVELS), kind)

    result = []
    for key in sorted(groups, key=order):
        members = groups[key]
        states: dict[str, int] = dict.fromkeys(MEMORY_STATES, 0)
        mastery: list[float] = []
        introduced = 0
        for item in members:
            facets = memories.get(item.id, ())
            status = learner_items.get(item.id)
            state = item_state(facets, status)
            states[state] += 1
            introduced += status == "introduced"
            value = item_mastery([f for f in facets if f.stability is not None])
            if value is not None:
                mastery.append(value)
        result.append(
            LevelProgress(
                level=key[0],
                kind=key[1],
                total=len(members),
                introduced=introduced,
                new=states["new"],
                learning=states["learning"],
                young=states["young"],
                mature=states["mature"],
                presumed_known=states["presumed_known"],
                mean_mastery=sum(mastery) / len(mastery) if mastery else None,
            )
        )
    return result


# --- mastery trajectory ------------------------------------------------------------------------


@dataclass(frozen=True)
class TrajectoryPoint:
    event_id: int
    ts: datetime
    kind: str
    outcome: str | None
    mastery: float
    stability: float | None
    n_eff: float


def mastery_trajectory(
    events: Iterable[EventData], cfg: ProjectionConfig, item_id: str, facet: str
) -> list[TrajectoryPoint]:
    """The (item, facet) state after each event, by folding `projection.apply`.

    The caller passes only non-voided events. The last point equals the stored `item_memory`
    state because this is the same fold as `projection.replay`. `status_change` events carry no
    state change and are left out of the points (they still advance the fold).
    """
    state = MemoryState.initial(item_id, facet)
    points = []
    for event in sorted(events, key=lambda e: (e.ts, e.id)):
        state, _ = apply(state, event, cfg)
        if event.kind == "status_change":
            continue
        points.append(
            TrajectoryPoint(
                event_id=event.id,
                ts=event.ts,
                kind=event.kind,
                outcome=event.outcome,
                mastery=state.mastery,
                stability=state.card.stability if state.card else None,
                n_eff=state.n_eff,
            )
        )
    return points
