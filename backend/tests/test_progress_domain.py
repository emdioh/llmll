from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.config import ProjectionConfig
from app.domain.progress import (
    ActivityEvent,
    FacetMemory,
    ItemInfo,
    ReviewOutcome,
    activity_by_day,
    due_forecast,
    item_mastery,
    item_state,
    level_progress,
    mastery_trajectory,
    memory_state,
    period_totals,
    reading_minutes,
    streaks,
    study_days,
    weekly_accuracy,
)
from app.domain.projection import EventData, replay
from app.domain.selection import MemoryView

ROME = ZoneInfo("Europe/Rome")
UTC_TZ = ZoneInfo("UTC")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def days(*numbers: int) -> set[date]:
    return {date(2026, 10, n) for n in numbers}


# --- study days and streaks ----------------------------------------------------------------------


def test_study_days_use_the_local_calendar_day() -> None:
    stamps = [utc(2026, 10, 5, 21, 59), utc(2026, 10, 5, 22, 0), utc(2026, 10, 5, 23, 30)]
    assert study_days(stamps, UTC_TZ) == {date(2026, 10, 5)}
    # Rome is UTC+2 in October: 23:59 local on the 5th, then midnight rolls over to the 6th.
    assert study_days(stamps, ROME) == {date(2026, 10, 5), date(2026, 10, 6)}
    # Naive timestamps (SQLite) are read as UTC.
    assert study_days([datetime(2026, 10, 5, 22, 0)], ROME) == {date(2026, 10, 6)}


def test_study_days_on_the_autumn_dst_change() -> None:
    """Europe/Rome, Sunday 25 Oct 2026: clocks go back at 03:00, the local day has 25 hours."""
    first_minute = utc(2026, 10, 24, 22, 0)  # 00:00 CEST
    last_minute = utc(2026, 10, 25, 22, 59)  # 23:59 CET
    next_day = utc(2026, 10, 25, 23, 0)  # 00:00 CET on the 26th
    assert study_days([first_minute, last_minute], ROME) == {date(2026, 10, 25)}
    assert study_days([first_minute, last_minute], UTC_TZ) == {
        date(2026, 10, 24),
        date(2026, 10, 25),
    }
    assert study_days([last_minute, next_day], ROME) == {date(2026, 10, 25), date(2026, 10, 26)}


def test_study_days_on_the_spring_dst_change() -> None:
    """Europe/Rome, Sunday 29 Mar 2026: clocks go forward at 02:00, the local day has 23 hours."""
    assert study_days([utc(2026, 3, 28, 23, 0), utc(2026, 3, 29, 21, 59)], ROME) == {
        date(2026, 3, 29)
    }
    assert utc(2026, 3, 29, 22, 0).astimezone(ROME).date() == date(2026, 3, 30)


def test_streaks_with_gaps() -> None:
    result = streaks(days(1, 2, 3, 5, 6), date(2026, 10, 6))
    assert (result.current, result.longest, result.last_study_day) == (2, 3, date(2026, 10, 6))


def test_streak_survives_a_day_not_studied_yet() -> None:
    # Studied the 4th and 5th, today is the 6th and nothing yet: the streak is still alive.
    assert streaks(days(4, 5), date(2026, 10, 6)).current == 2
    # Once today is studied it grows.
    assert streaks(days(4, 5, 6), date(2026, 10, 6)).current == 3
    # A whole day without study breaks it.
    broken = streaks(days(3, 4), date(2026, 10, 6))
    assert (broken.current, broken.longest, broken.last_study_day) == (0, 2, date(2026, 10, 4))


def test_streaks_edge_cases() -> None:
    empty = streaks(set(), date(2026, 10, 6))
    assert (empty.current, empty.longest, empty.last_study_day) == (0, 0, None)
    # Days after "today" (clock skew, other time zone) are ignored.
    assert streaks(days(5, 6, 7, 8), date(2026, 10, 6)).current == 2
    assert streaks(days(6), date(2026, 10, 6)).longest == 1


def test_streak_runs_across_the_dst_change_day() -> None:
    stamps = [utc(2026, 10, 24, 12), utc(2026, 10, 25, 12), utc(2026, 10, 26, 12)]
    result = streaks(study_days(stamps, ROME), date(2026, 10, 26))
    assert (result.current, result.longest) == (3, 3)


def test_streak_depends_on_the_time_zone_around_midnight() -> None:
    stamps = [utc(2026, 10, 4, 12), utc(2026, 10, 5, 22, 30)]
    in_utc = streaks(study_days(stamps, UTC_TZ), date(2026, 10, 5))
    in_rome = streaks(study_days(stamps, ROME), date(2026, 10, 6))
    assert in_utc.current == 2  # the 4th and the 5th
    assert in_rome.current == 1 and in_rome.last_study_day == date(2026, 10, 6)  # 4th, 6th: gap


# --- activity ------------------------------------------------------------------------------------


def test_activity_by_day_aggregates_per_local_day() -> None:
    events = [
        ActivityEvent(utc(2026, 10, 5, 9), "review", 0.5),
        ActivityEvent(utc(2026, 10, 5, 9, 1), "review", 0.25),
        ActivityEvent(utc(2026, 10, 5, 9, 2), "exercise", 1.0),
        ActivityEvent(utc(2026, 10, 5, 9, 3), "new_item"),
        ActivityEvent(utc(2026, 10, 6, 9), "intro", 0.1),
        ActivityEvent(utc(2026, 10, 7, 9), "new_item"),  # an introduction alone is no study
        ActivityEvent(utc(2026, 10, 8, 9), "reading", 12.0),
        ActivityEvent(utc(2026, 10, 20, 9), "review"),  # outside the range
    ]
    rows = activity_by_day(events, UTC_TZ, date(2026, 10, 4), date(2026, 10, 9))
    assert [r.date.day for r in rows] == [4, 5, 6, 7, 8, 9]  # empty days included
    by_day = {r.date.day: r for r in rows}
    assert (by_day[5].reviews, by_day[5].exercises, by_day[5].new_items) == (2, 1, 1)
    assert by_day[5].minutes == 1.8 and by_day[5].active
    assert by_day[6].active and by_day[6].reviews == 0 and by_day[6].minutes == 0.1
    assert by_day[7].new_items == 1 and not by_day[7].active
    assert (by_day[8].readings, by_day[8].minutes, by_day[8].active) == (1, 12.0, True)
    assert not by_day[4].active and not by_day[9].active


def test_activity_by_day_in_a_time_zone_and_across_dst() -> None:
    events = [
        ActivityEvent(utc(2026, 10, 24, 22, 30), "review"),  # 00:30 CEST on the 25th
        ActivityEvent(utc(2026, 10, 25, 22, 30), "review"),  # 23:30 CET on the 25th
        ActivityEvent(utc(2026, 10, 25, 23, 30), "review"),  # 00:30 CET on the 26th
    ]
    rows = activity_by_day(events, ROME, date(2026, 10, 24), date(2026, 10, 26))
    assert [(r.date.day, r.reviews) for r in rows] == [(24, 0), (25, 2), (26, 1)]


def test_reading_minutes_are_capped() -> None:
    start = utc(2026, 10, 5, 9)
    assert reading_minutes(start, start + timedelta(minutes=7)) == 7.0
    assert reading_minutes(start, start + timedelta(hours=5)) == 60.0
    assert reading_minutes(start, start - timedelta(minutes=1)) == 0.0


def test_period_totals_sum_the_requested_days() -> None:
    events = [
        ActivityEvent(utc(2026, 10, 1, 9), "review", 1.0),
        ActivityEvent(utc(2026, 10, 3, 9), "exercise", 2.0),
        ActivityEvent(utc(2026, 10, 3, 10), "reading", 3.0),
        ActivityEvent(utc(2026, 10, 9, 9), "review", 4.0),
    ]
    rows = activity_by_day(events, UTC_TZ, date(2026, 10, 1), date(2026, 10, 10))
    total = period_totals(rows, date(2026, 10, 1), date(2026, 10, 7))
    assert (total.study_days, total.reviews, total.exercises, total.readings) == (2, 1, 1, 1)
    assert total.minutes == 6.0


def test_weekly_accuracy_by_monday_week() -> None:
    outcomes = [
        ReviewOutcome(utc(2026, 10, 5, 9), "flashcard", "correct"),  # Monday
        ReviewOutcome(utc(2026, 10, 6, 9), "flashcard", "error"),
        ReviewOutcome(utc(2026, 10, 7, 9), "production", "correct"),
        ReviewOutcome(utc(2026, 10, 11, 22, 30), "production", "assisted"),  # Sunday UTC
        ReviewOutcome(utc(2026, 10, 12, 9), "production", "correct"),  # next week
    ]
    rows = weekly_accuracy(outcomes, UTC_TZ, date(2026, 9, 30), date(2026, 10, 12))
    assert [r.week_start for r in rows] == [
        date(2026, 9, 28),
        date(2026, 10, 5),
        date(2026, 10, 12),
    ]
    assert rows[0].n == 0 and rows[0].flashcards_correct_rate is None
    assert rows[1].flashcards_correct_rate == 0.5 and rows[1].production_correct_rate == 0.5
    assert (rows[1].flashcards_n, rows[1].production_n, rows[1].n) == (2, 2, 4)
    assert rows[2].production_correct_rate == 1.0 and rows[2].flashcards_correct_rate is None
    # In Rome the Sunday 22:30 UTC review is already Monday 00:30 local: it counts in next week.
    rome = weekly_accuracy(outcomes, ROME, date(2026, 10, 5), date(2026, 10, 12))
    assert [r.production_n for r in rome] == [1, 2]


# --- memory states -------------------------------------------------------------------------------


def facet(stability: float | None, mastery: float = 0.5, name: str = "recognition", **kw):
    return FacetMemory(name, stability, mastery, **kw)


def test_memory_states() -> None:
    assert memory_state(None) == "new"
    assert memory_state(None, presumed_known=True) == "presumed_known"
    assert memory_state(facet(None)) == "new"
    assert memory_state(facet(None), presumed_known=True) == "presumed_known"
    assert memory_state(facet(0.2)) == "learning"
    assert memory_state(facet(0.999)) == "learning"
    assert memory_state(facet(1.0)) == "young"
    assert memory_state(facet(20.9)) == "young"
    assert memory_state(facet(21.0)) == "mature"
    assert memory_state(facet(400.0)) == "mature"


def test_item_state_is_that_of_the_least_stable_facet() -> None:
    both = [facet(30.0, name="recognition"), facet(3.0, name="production")]
    assert item_state(both, "introduced") == "young"
    assert item_state([facet(30.0)], "introduced") == "mature"
    assert item_state([], "presumed_known") == "presumed_known"
    assert item_state([], "candidate") == "new"
    # A card beats the declared status.
    assert item_state([facet(0.5)], "presumed_known") == "learning"
    assert item_mastery([facet(1, 0.2), facet(1, 0.6)]) == pytest.approx(0.4)
    assert item_mastery([]) is None


# --- forecast ------------------------------------------------------------------------------------


def view(due: datetime | None) -> MemoryView:
    return MemoryView(item_id="x", facet="recognition", due=due, card=None)


def test_due_forecast_counts_overdue_on_day_zero() -> None:
    now = utc(2026, 10, 5, 9)
    memories = [
        view(utc(2026, 9, 1)),  # long overdue
        view(utc(2026, 10, 5, 8)),  # overdue today
        view(utc(2026, 10, 5, 23)),  # later today
        view(utc(2026, 10, 6, 1)),
        view(utc(2026, 10, 7, 12)),
        view(utc(2026, 10, 7, 13)),
        view(utc(2026, 10, 20)),  # beyond the window
        view(None),  # no card
    ]
    forecast = due_forecast(memories, now, UTC_TZ, 4)
    assert [(f["date"].day, f["due"]) for f in forecast] == [(5, 3), (6, 1), (7, 2), (8, 0)]


def test_due_forecast_uses_local_days_across_dst() -> None:
    now = utc(2026, 10, 24, 20, 0)  # 22:00 CEST on the 24th
    memories = [
        view(utc(2026, 10, 24, 21, 0)),  # 23:00 CEST on the 24th
        view(utc(2026, 10, 25, 22, 30)),  # 23:30 CET on the 25th
        view(utc(2026, 10, 25, 23, 30)),  # 00:30 CET on the 26th
        view(utc(2026, 10, 30)),
    ]
    forecast = due_forecast(memories, now, ROME, 3)
    assert [(f["date"].day, f["due"]) for f in forecast] == [(24, 1), (25, 1), (26, 1)]


# --- level progress ------------------------------------------------------------------------------


def test_level_progress_per_level_and_kind() -> None:
    items = [
        ItemInfo("lex:a", "lemma", "A1"),
        ItemInfo("lex:b", "lemma", "A1"),
        ItemInfo("lex:c", "lemma", "A1"),
        ItemInfo("lex:d", "lemma", "A1"),
        ItemInfo("lex:e", "lemma", "A2"),
        ItemInfo("gram:x", "grammar", "A1"),
    ]
    statuses = {
        "lex:a": "introduced",
        "lex:b": "introduced",
        "lex:c": "presumed_known",
        "lex:d": "candidate",
        "gram:x": "introduced",
    }
    memories = {
        "lex:a": [facet(40.0, 0.9, name="recognition"), facet(30.0, 0.7, name="production")],
        "lex:b": [facet(0.4, 0.3)],
        "gram:x": [facet(5.0, 0.5, name="production")],
    }
    rows = {(r.level, r.kind): r for r in level_progress(items, statuses, memories)}
    assert list(rows) == [("A1", "grammar"), ("A1", "lemma"), ("A2", "lemma")]  # CEFR order
    a1 = rows[("A1", "lemma")]
    assert (a1.total, a1.introduced, a1.mature, a1.learning, a1.young) == (4, 2, 1, 1, 0)
    assert (a1.presumed_known, a1.new) == (1, 1)
    assert a1.mean_mastery == pytest.approx((0.8 + 0.3) / 2)  # items with a card only
    grammar = rows[("A1", "grammar")]
    assert (grammar.young, grammar.introduced, grammar.mean_mastery) == (1, 1, 0.5)
    a2 = rows[("A2", "lemma")]
    assert (a2.total, a2.new, a2.mean_mastery) == (1, 1, None)


# --- trajectory ----------------------------------------------------------------------------------


def events_of_an_item() -> list[EventData]:
    t0 = utc(2026, 10, 5, 9)
    return [
        EventData(1, t0, "introduce"),
        EventData(2, t0 + timedelta(days=1), "review", "correct", 0.5),
        EventData(3, t0 + timedelta(days=3), "review", "error", 0.8, ("m",)),
        EventData(4, t0 + timedelta(days=3), "status_change", presumed_known=True),
        EventData(5, t0 + timedelta(days=4), "implicit", "correct", 0.2),
        EventData(6, t0 + timedelta(days=9), "lookup", "assisted", 0.5),
        EventData(7, t0 + timedelta(days=9, hours=1), "review", "correct", 1.0, confidence=0.4),
    ]


def test_mastery_trajectory_ends_in_the_projection_state() -> None:
    cfg = ProjectionConfig()
    events = events_of_an_item()
    points = mastery_trajectory(events, cfg, "lex:x", "recognition")
    final = replay(events, cfg, "lex:x", "recognition")
    assert points[-1].mastery == final.mastery and points[-1].n_eff == final.n_eff
    assert points[-1].stability == final.card.stability
    assert points[-1].event_id == 7
    # One point per event except status_change, with the state right after each event.
    assert [p.event_id for p in points] == [1, 2, 3, 5, 6, 7]
    assert [p.kind for p in points][:3] == ["introduce", "review", "review"]
    assert points[0].mastery == 0.5 and points[0].stability is not None
    for point in points:
        prefix = [e for e in events if e.id <= point.event_id]
        state = replay(prefix, cfg, "lex:x", "recognition")
        assert (point.mastery, point.n_eff) == (state.mastery, state.n_eff)
    assert points[2].mastery < points[1].mastery  # the error lowers mastery
    # The order of the input does not matter (replay sorts by timestamp and id).
    assert mastery_trajectory(list(reversed(events)), cfg, "lex:x", "recognition") == points


def test_mastery_trajectory_of_nothing() -> None:
    assert mastery_trajectory([], ProjectionConfig(), "lex:x", "recognition") == []
