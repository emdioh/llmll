import json
from datetime import UTC, datetime, timedelta

import pytest
from fsrs import Rating

from app.cli import cmd_optimize_fsrs
from app.cli import main as cli_main
from app.config import Settings, get_settings
from app.domain.config import ProjectionConfig
from app.domain.optimize import build_review_logs
from app.domain.projection import EventData
from app.domain.scheduling import card_id_for, get_scheduler, new_card, retrievability, review
from app.services.learner import create_learner, projection_config, settings_of, update_settings
from app.store.db import create_session_factory
from app.store.events import append_event
from app.store.models import Learner, LearningEvent

from .conftest import import_fixture

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def event(id_: int, days: float, kind: str, outcome: str | None = None, **kw) -> EventData:
    return EventData(id_, T0 + timedelta(days=days), kind, outcome, evidence_weight=1.0, **kw)


CFG = ProjectionConfig()


def test_review_logs_follow_the_projection() -> None:
    events = {
        ("lex:a", "production"): [
            event(1, 0, "introduce"),
            event(2, 3, "review", "correct"),
            event(3, 9, "review", "assisted"),
            event(4, 20, "review", "error"),
            event(5, 21, "status_change"),  # no scheduler review
        ],
        ("lex:b", "recognition"): [
            event(6, 1, "introduce"),
            event(7, 5, "review", "correct", confidence=0.2),  # too uncertain: no rating
            event(8, 6, "implicit", "correct"),
        ],
    }
    logs = build_review_logs(events, CFG)
    a = card_id_for("lex:a", "production")
    b = card_id_for("lex:b", "recognition")
    assert [(log.card_id, log.rating) for log in logs] == [
        (a, Rating.Good),  # introduce = first review
        (b, Rating.Good),
        (a, Rating.Good),
        (b, Rating.Good),  # implicit, rated like a review
        (a, Rating.Hard),
        (a, Rating.Again),
    ]
    assert [log.review_datetime for log in logs] == sorted(log.review_datetime for log in logs)


def test_review_logs_ignore_second_introduce_and_unordered_input() -> None:
    events = {
        ("lex:a", "production"): [
            event(3, 5, "review", "correct"),
            event(2, 0, "introduce"),
            event(1, 0, "introduce"),
        ]
    }
    ratings = [log.rating for log in build_review_logs(events, CFG)]
    assert ratings == [Rating.Good, Rating.Good]


def test_fitted_parameters_change_the_scheduler_and_the_projection_version() -> None:
    defaults = ProjectionConfig()
    base = list(get_scheduler(0.85).parameters)
    base[2] = 3.0  # initial stability after a Good first review
    fitted = ProjectionConfig(fsrs_parameters=tuple(base))
    assert defaults.version != fitted.version
    card = review(new_card(1), Rating.Good, T0, 0.85)
    card2 = review(new_card(1), Rating.Good, T0, 0.85, fitted.fsrs_parameters)
    later = T0 + timedelta(days=5)
    assert card.stability != card2.stability
    assert retrievability(card, later, 0.85) != retrievability(
        card2, later, 0.85, fitted.fsrs_parameters
    )


# --- the command ---------------------------------------------------------------------------------


@pytest.fixture
def db_env(migrated_settings: Settings, monkeypatch) -> Settings:
    monkeypatch.setenv("LLMLL_DATABASE_URL", migrated_settings.database_url)
    get_settings.cache_clear()
    yield migrated_settings
    get_settings.cache_clear()


def seed(settings: Settings, n_reviews: int) -> None:
    import_fixture(settings)
    with create_session_factory(settings)() as db:
        learner = create_learner(db, "A1", ["it"], "it", T0)
        cfg = projection_config(settings_of(learner))
        append_event(
            db, cfg, learner_id=1, item_id="lex:tisch", facet="recognition", ts=T0, kind="introduce"
        )
        for n in range(n_reviews):
            append_event(
                db,
                cfg,
                learner_id=1,
                item_id="lex:tisch",
                facet="recognition",
                ts=T0 + timedelta(days=n + 1),
                kind="review",
                outcome="correct",
                evidence_weight=1.0,
            )
        db.commit()


def test_optimize_refuses_below_min_reviews(db_env: Settings, capsys) -> None:
    seed(db_env, 5)
    assert cli_main(["optimize-fsrs"]) == 1
    err = capsys.readouterr().err
    assert "refusing to optimize: 6 reviews, at least 1000" in err
    assert cli_main(["optimize-fsrs", "--min-reviews", "7"]) == 1
    assert "at least 7" in capsys.readouterr().err


def test_optimize_without_learner(db_env: Settings, capsys) -> None:
    assert cli_main(["optimize-fsrs"]) == 1
    assert "no learner" in capsys.readouterr().err


def test_optimize_explains_missing_optimizer(db_env: Settings, capsys, monkeypatch) -> None:
    seed(db_env, 5)

    def missing():
        raise RuntimeError("the FSRS optimizer is not installed ... uv sync --group optimizer")

    monkeypatch.setattr("app.cli._load_optimizer", missing)
    assert cli_main(["optimize-fsrs", "--min-reviews", "3"]) == 1
    assert "uv sync --group optimizer" in capsys.readouterr().err


def test_optimize_applies_parameters_and_replays(db_env: Settings, capsys, monkeypatch) -> None:
    seed(db_env, 5)
    fitted = [p * 1.01 for p in get_scheduler(0.85).parameters]

    class FakeOptimizer:
        def __init__(self, logs) -> None:
            self.logs = logs

        def compute_optimal_parameters(self):
            return fitted

        def _compute_batch_loss(self, *, parameters):
            return 0.5 if parameters == fitted else 0.6

    monkeypatch.setattr("app.cli._load_optimizer", lambda: FakeOptimizer)
    assert cli_main(["optimize-fsrs", "--min-reviews", "3"]) == 0
    assert "0.6000 -> 0.5000" in capsys.readouterr().out
    with create_session_factory(db_env)() as db:
        assert db.get(Learner, 1).settings.get("fsrs_parameters") is None  # not applied

    assert cli_main(["optimize-fsrs", "--min-reviews", "3", "--apply"]) == 0
    with create_session_factory(db_env)() as db:
        learner = db.get(Learner, 1)
        assert learner.settings["fsrs_parameters"] == fitted
        cfg = projection_config(settings_of(learner))
        assert cfg.fsrs_parameters == tuple(fitted)
        from app.store.models import ItemMemory

        memory = db.get(ItemMemory, (1, "lex:tisch", "recognition"))
        assert memory.projection_version == cfg.version  # replayed with the new parameters
        assert db.query(LearningEvent).count() == 6  # events untouched


def test_optimize_does_not_apply_a_worse_fit(db_env: Settings, capsys, monkeypatch) -> None:
    seed(db_env, 5)

    class Worse:
        def __init__(self, logs) -> None: ...
        def compute_optimal_parameters(self):
            return [0.5] * 21

        def _compute_batch_loss(self, *, parameters):
            return 0.5 if parameters != [0.5] * 21 else 0.9

    monkeypatch.setattr("app.cli._load_optimizer", lambda: Worse)
    assert cli_main(["optimize-fsrs", "--min-reviews", "3", "--apply"]) == 1
    with create_session_factory(db_env)() as db:
        assert db.get(Learner, 1).settings.get("fsrs_parameters") is None


def test_update_settings_roundtrip_keeps_parameters(db_env: Settings) -> None:
    seed(db_env, 1)
    with create_session_factory(db_env)() as db:
        learner = db.get(Learner, 1)
        update_settings(db, learner, {"fsrs_parameters": list(get_scheduler(0.85).parameters)})
        update_settings(db, learner, {"review_cap": 9})
        assert settings_of(learner).fsrs_parameters == list(get_scheduler(0.85).parameters)
        assert json.dumps(learner.settings)  # JSON-serialisable
        assert cmd_optimize_fsrs  # command importable
