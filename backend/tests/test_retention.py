from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from fsrs import Card
from sqlalchemy import select

from app.config import Settings
from app.domain.config import ProjectionConfig
from app.domain.retention import RetentionEvent, bucket_index, retention_report
from app.domain.selection import MemoryView
from app.store.db import create_session_factory
from app.store.models import LearningEvent

from .conftest import Clock
from .test_api_flow import answer, setup_learner

NOW = datetime(2026, 10, 5, tzinfo=UTC)


def ev(outcome: str | None, predicted: float | None, kind: str = "review") -> RetentionEvent:
    return RetentionEvent(kind, outcome, predicted)


def test_bucket_index() -> None:
    assert [bucket_index(p) for p in (0.0, 0.05, 0.1, 0.55, 0.99, 1.0)] == [0, 0, 1, 5, 9, 9]


def test_retention_report_buckets_and_observed_retention() -> None:
    events = [
        ev("correct", 0.95),
        ev("correct", 0.91),
        ev("error", 0.93),
        ev("assisted", 0.97),
        ev("correct", 0.72),
        ev("error", 0.78),
        ev("correct", 1.0),
        # ignored: no prediction, not a review, no outcome
        ev("correct", None),
        ev("correct", 0.9, kind="implicit"),
        ev(None, 0.9),
    ]
    memories = [
        MemoryView("a", "production", NOW - timedelta(days=1), Card()),
        MemoryView("b", "production", NOW + timedelta(days=1), Card()),
        MemoryView("c", "production", None, None),
    ]
    cfg = ProjectionConfig(desired_retention=0.9)
    report = retention_report(events, memories, cfg, NOW)
    assert report.n_reviews == 7 and report.target_retention == 0.9 and report.due_now == 1
    assert report.observed_retention == pytest.approx(4 / 7)  # assisted does not count as recalled
    low, high = report.calibration
    assert (low.low, low.high, low.n) == (0.7, 0.8, 2)
    assert (low.recalled, low.forgotten, low.assisted) == (1, 1, 0)
    assert low.predicted == pytest.approx(0.75) and low.observed == 0.5
    assert (high.low, high.high, high.n) == (0.9, 1.0, 5)
    assert (high.recalled, high.assisted, high.forgotten) == (3, 1, 1)
    assert high.observed == pytest.approx(3 / 5)


def test_retention_report_without_data() -> None:
    report = retention_report([], [], ProjectionConfig())
    assert report.n_reviews == 0 and report.observed_retention is None
    assert report.calibration == () and report.due_now == 0


# --- predicted_retrievability on events and GET /api/stats ---------------------------------------


def wrong_choice(client: TestClient, settings: Settings, card: dict) -> int:
    from app.store.models import Exercise

    with create_session_factory(settings)() as db:
        correct = db.get(Exercise, card["exercise_id"]).solution["correct_index"]
    return (correct + 1) % 4


def right_choice(settings: Settings, card: dict) -> int:
    from app.store.models import Exercise

    with create_session_factory(settings)() as db:
        return db.get(Exercise, card["exercise_id"]).solution["correct_index"]


def test_stats_and_predicted_retrievability(
    curriculum_client: TestClient, migrated_settings: Settings, clock: Clock
) -> None:
    client = curriculum_client
    setup_learner(client, "A1")
    assert client.get("/api/stats").json()["observed_retention"] is None

    # Day 0: intro cards only.
    session = client.post("/api/sessions").json()
    for card in session["cards"]:
        answer(client, session["session_id"], card)
    empty = client.get("/api/stats").json()
    assert empty["reviews_7d"] == 0 and empty["calibration"] == []
    assert empty["new_items_7d"] == len(session["cards"]) and empty["sessions_7d"] == 1

    # Day 10 and day 20: recognition reviews, half of them wrong.
    reviewed = last_day = 0
    for _ in range(2):
        clock.advance(days=10)
        client.put("/api/settings", json={"review_cap": 8})
        session = client.post("/api/sessions").json()
        cards = [c for c in session["cards"] if c["type"] == "flashcard_recognition"]
        assert cards
        last_day = len(cards)
        for n, card in enumerate(cards):
            choice = (
                right_choice(migrated_settings, card)
                if n % 2
                else wrong_choice(client, migrated_settings, card)
            )
            answer(client, session["session_id"], card, answer={"choice": choice})
            reviewed += 1

    with create_session_factory(migrated_settings)() as db:
        reviews = db.scalars(select(LearningEvent).where(LearningEvent.kind == "review")).all()
        introduces = db.scalars(
            select(LearningEvent).where(LearningEvent.kind == "introduce")
        ).all()
    assert len(reviews) == reviewed
    # Every review followed an introduction, so a card existed and a prediction was stored.
    assert all(e.predicted_retrievability is not None for e in reviews)
    assert all(0.0 < e.predicted_retrievability <= 1.0 for e in reviews)
    assert all(e.predicted_retrievability is None for e in introduces)

    stats = client.get("/api/stats").json()
    assert stats["reviews_7d"] == last_day  # only the day-20 session is inside 7 days
    assert stats["reviews_30d"] == reviewed
    assert stats["sessions_7d"] == 1 and stats["readings_7d"] == 0
    assert stats["target_retention"] == 0.85
    assert sum(b["n"] for b in stats["calibration"]) == reviewed
    assert 0.0 < stats["observed_retention"] < 1.0
    assert stats["observed_retention"] == pytest.approx(
        sum(b["recalled"] for b in stats["calibration"]) / reviewed
    )
    for bucket in stats["calibration"]:
        assert bucket["bucket_low"] <= bucket["predicted"] <= bucket["bucket_high"]
    assert stats["backlog"] >= 0


def test_stats_requires_learner(client: TestClient) -> None:
    assert client.get("/api/stats").status_code == 404
