import random
from datetime import UTC, datetime, timedelta

import pytest
from fsrs import Rating

from app.domain.answers import check_production, check_recognition
from app.domain.budget import new_item_budget
from app.domain.config import LearnerSettings, ProjectionConfig
from app.domain.grading import grade
from app.domain.mastery import update_mastery, update_tag_counts
from app.domain.projection import EventData, MemoryState, apply, replay
from app.domain.scheduling import card_id_for, new_card, retrievability, review
from app.domain.selection import MemoryView, select_due

CFG = ProjectionConfig()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


# --- mastery ---------------------------------------------------------------


def test_mastery_update_and_n_eff() -> None:
    m, n = update_mastery(0.5, 0.0, "correct", 0.8, 1.0, CFG)
    assert m == pytest.approx(0.5 + 0.3 * 0.8 * 0.5)
    assert n == pytest.approx(0.8)
    m2, n2 = update_mastery(m, n, "error", 0.8, 1.0, CFG)
    assert m2 == pytest.approx(m + 0.24 * (0 - m))
    assert n2 == pytest.approx(0.9 * 0.8 + 0.8)


def test_mastery_assisted_moves_toward_half() -> None:
    m, _ = update_mastery(1.0, 0.0, "assisted", 1.0, 1.0, CFG)
    assert m == pytest.approx(1.0 - 0.3 * 0.5)


def test_mastery_clamps_step_and_uncertain_weight() -> None:
    cfg = ProjectionConfig(alpha=2.0)
    m, _ = update_mastery(0.5, 0.0, "correct", 1.0, 1.0, cfg)
    assert m == pytest.approx(1.0)
    _, n = update_mastery(0.5, 0.0, "correct", 1.0, 0.4, CFG)
    assert n == pytest.approx(0.4)  # weight * confidence for uncertain events


def test_tag_error_counts() -> None:
    counts = update_tag_counts({}, "error", ("gender", "acc"), CFG)
    assert counts == {"gender": 1.0, "acc": 1.0}
    counts = update_tag_counts(counts, "correct", (), CFG)
    assert counts["gender"] == pytest.approx(0.9)
    counts = update_tag_counts({"x": 0.052}, "correct", (), CFG)
    assert counts == {}


# --- grading (R§4.3) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "mastery", "n_eff", "confidence", "presumed", "first", "rating", "remed"),
    [
        ("correct", 0.5, 0, 1.0, False, False, Rating.Good, False),
        ("correct", 0.5, 0, 1.0, True, True, Rating.Easy, False),
        ("correct", 0.5, 0, 1.0, True, False, Rating.Good, False),
        ("correct", 0.5, 0, 1.0, False, True, Rating.Good, False),
        ("assisted", 0.5, 0, 1.0, False, False, Rating.Hard, False),
        ("error", 0.5, 0, 1.0, False, False, Rating.Again, True),
        ("error", 0.9, 2.0, 1.0, False, False, Rating.Again, True),  # not enough evidence
        ("error", 0.7, 5.0, 1.0, False, False, Rating.Again, True),  # mastery too low
        ("error", 0.75, 3.0, 1.0, False, False, Rating.Hard, False),  # slip
        ("error", 0.9, 8.0, 1.0, False, False, Rating.Hard, False),
        ("correct", 0.5, 0, 0.49, False, False, None, False),  # uncertain
        ("error", 0.5, 0, 0.2, False, False, None, False),
    ],
)
def test_grading_table(outcome, mastery, n_eff, confidence, presumed, first, rating, remed) -> None:
    decision = grade(outcome, mastery, n_eff, confidence, presumed, first, CFG)
    assert decision.rating == rating
    assert decision.needs_remediation is remed


# --- scheduling --------------------------------------------------------------


def test_scheduler_has_no_learning_steps_and_is_deterministic() -> None:
    cid = card_id_for("lex:tisch", "recognition")
    assert cid == card_id_for("lex:tisch", "recognition")
    assert cid != card_id_for("lex:tisch", "production")
    a = review(new_card(cid), Rating.Good, T0, 0.85)
    b = review(new_card(cid), Rating.Good, T0, 0.85)
    assert a.to_dict() == b.to_dict()
    assert a.due >= T0 + timedelta(days=1)  # straight into review, no minutes-long steps
    assert retrievability(a, a.due, 0.85) == pytest.approx(0.85, abs=0.02)


# --- budget ------------------------------------------------------------------


def test_budget() -> None:
    s = LearnerSettings(weekly_new_lemmas=20, weekly_new_grammar=2, review_cap=15)
    assert tuple(vars(new_item_budget(0, 0, 0, s)).values()) == (20, 2)
    assert tuple(vars(new_item_budget(15, 3, 0, s)).values()) == (5, 0)
    assert new_item_budget(25, 0, 0, s).lemmas == 0
    assert tuple(vars(new_item_budget(0, 0, 15, s)).values()) == (
        20,
        2,
    )  # at the cap: no reduction yet
    assert tuple(vars(new_item_budget(0, 0, 16, s)).values()) == (10, 1)
    assert tuple(vars(new_item_budget(5, 1, 20, s)).values()) == (7, 0)  # floor
    assert tuple(vars(new_item_budget(0, 0, 31, s)).values()) == (0, 0)


# --- selection ---------------------------------------------------------------


def test_select_due_order() -> None:
    now = T0 + timedelta(days=30)

    def view(item_id: str, days_ago_reviewed: int, zipf: float | None, due_in: int = -1):
        card = review(new_card(1), Rating.Good, now - timedelta(days=days_ago_reviewed), 0.85)
        return MemoryView(item_id, "production", now + timedelta(days=due_in), card, zipf)

    items = [
        view("lex:b", 10, 5.0),
        view("lex:a", 10, 5.0),
        view("lex:rare", 10, 3.0),
        view("lex:old", 25, 1.0),  # lowest retrievability wins first
        view("lex:future", 1, 9.0, due_in=3),  # not due
    ]
    ordered = select_due(items, now, limit=10)
    assert [m.item_id for m in ordered] == ["lex:old", "lex:a", "lex:b", "lex:rare"]
    assert len(select_due(items, now, limit=2)) == 2


# --- answers -----------------------------------------------------------------


def test_recognition() -> None:
    assert check_recognition(2, 2) == "correct"
    assert check_recognition(1, 2) == "error"
    assert check_recognition(None, 0) == "error"


@pytest.mark.parametrize(
    ("answer", "lemma", "gender", "expected"),
    [
        ("der Tisch", "Tisch", "m", ("correct", [])),
        ("  Der   TISCH ", "Tisch", "m", ("correct", [])),
        ("die Tisch", "Tisch", "m", ("error", ["gender"])),
        ("Tisch", "Tisch", "m", ("error", ["article_missing"])),
        ("der Maedchen", "Mädchen", "n", ("error", ["gender", "spelling"])),
        ("das Maedchen", "Mädchen", "n", ("assisted", ["spelling"])),
        ("das Madchen", "Mädchen", "n", ("assisted", ["spelling"])),
        ("das Tich", "Tisch", "m", ("error", ["gender", "spelling"])),
        ("der Tich", "Tisch", "m", ("assisted", ["spelling"])),
        ("die Straße", "Straße", "f", ("correct", [])),
        ("die Strasse", "Straße", "f", ("correct", [])),
        ("die Eltern", "Eltern", "pl", ("correct", [])),
        ("der Stuhl", "Tisch", "m", ("error", [])),
        ("gehen", "gehen", None, ("correct", [])),
        ("gehn", "gehen", None, ("assisted", ["spelling"])),
        ("schon", "schön", None, ("assisted", ["spelling"])),
        ("laufen", "gehen", None, ("error", [])),
        ("an", "ab", None, ("error", [])),  # no typo tolerance for very short words
    ],
)
def test_production(answer, lemma, gender, expected) -> None:
    assert check_production(answer, lemma, gender) == expected


# --- projection --------------------------------------------------------------


def _random_events(seed: int, n: int = 40) -> list[EventData]:
    rng = random.Random(seed)
    ts = T0
    events = []
    for i in range(1, n + 1):
        ts += timedelta(hours=rng.randint(1, 200))
        kind = rng.choice(["review"] * 6 + ["implicit", "lookup", "introduce", "status_change"])
        outcome = rng.choice(["correct", "correct", "assisted", "error", None])
        events.append(
            EventData(
                id=i,
                ts=ts,
                kind=kind,
                outcome=outcome,
                evidence_weight=rng.choice([0.2, 0.5, 0.8, 1.2]),
                diagnostic_tags=tuple(rng.sample(["gender", "acc", "dat", "spelling"], 2)),
                presumed_known=rng.random() < 0.3,
                confidence=rng.choice([1.0, 1.0, 0.3, 0.7]),
            )
        )
    return events


@pytest.mark.parametrize("seed", range(25))
def test_incremental_equals_replay(seed: int) -> None:
    events = _random_events(seed)
    state = MemoryState.initial("lex:tisch", "production")
    for event in events:
        state, _ = apply(state, event, CFG)
    replayed = replay(events, CFG, "lex:tisch", "production")
    assert state == replayed
    shuffled = random.Random(seed).sample(events, len(events))
    assert replay(shuffled, CFG, "lex:tisch", "production") == replayed  # sorted by (ts, id)
    assert replay(events, CFG, "lex:tisch", "production") == replayed  # replay twice


def test_introduce_creates_card_without_touching_mastery() -> None:
    state = MemoryState.initial("lex:tisch", "recognition")
    new, decision = apply(state, EventData(1, T0, "introduce"), CFG)
    assert decision is None
    assert new.card is not None and new.card.due > T0
    assert (new.mastery, new.n_eff) == (state.mastery, state.n_eff)
    again, _ = apply(new, EventData(2, T0 + timedelta(hours=1), "introduce"), CFG)
    assert again.card == new.card  # second introduce does not review again


def test_presumed_known_first_review_is_easy() -> None:
    state = MemoryState.initial("lex:tisch", "production")
    ev = EventData(1, T0, "review", "correct", 0.8, presumed_known=True)
    easy, decision = apply(state, ev, CFG)
    assert decision.rating == Rating.Easy
    good, _ = apply(state, EventData(1, T0, "review", "correct", 0.8), CFG)
    assert easy.card.due > good.card.due


def test_uncertain_event_updates_mastery_but_not_card() -> None:
    state = MemoryState.initial("lex:tisch", "production")
    new, decision = apply(state, EventData(1, T0, "review", "error", 1.0, confidence=0.3), CFG)
    assert decision.rating is None and new.card is None
    assert new.mastery < state.mastery


def test_projection_version_changes_with_retention() -> None:
    assert ProjectionConfig().version == ProjectionConfig().version
    assert ProjectionConfig(desired_retention=0.9).version != ProjectionConfig().version
    assert ProjectionConfig(alpha=0.2).version != ProjectionConfig().version
