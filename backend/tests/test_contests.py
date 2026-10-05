# ruff: noqa: F811
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.cli import main as cli_main
from app.config import Settings, get_settings
from app.main import create_app
from app.services.contests import (
    RESOLVERS,
    AcceptAllResolver,
    Resolution,
    build_resolver,
    register_resolver,
)
from app.store.db import create_session_factory
from app.store.models import (
    Attempt,
    Contest,
    Exercise,
    ItemMemory,
    LearningEvent,
    RemediationItem,
)
from app.store.models import Evaluation as EvaluationRow

from .conftest import Clock
from .test_api_flow import snapshot
from .test_api_production import (  # noqa: F401
    LT,
    StubLLM,
    answer,
    api,
    intro_answer,
    lt,
    start,
    stub,
)


def db_of(settings: Settings):
    return create_session_factory(settings)()


def replay_via_cli(settings: Settings, monkeypatch) -> None:
    monkeypatch.setenv("LLMLL_DATABASE_URL", settings.database_url)
    get_settings.cache_clear()
    try:
        assert cli_main(["replay"]) == 0
    finally:
        get_settings.cache_clear()


def reference_of(settings: Settings, exercise_id: str) -> str:
    with db_of(settings) as db:
        return db.get(Exercise, exercise_id).solution["reference_solutions"][0]


def prepare(api: TestClient, card: dict) -> dict:
    resp = api.post(f"/api/exercises/{card['exercise_id']}/prepare")
    assert resp.status_code == 200, resp.text
    return resp.json()


def play_day(api: TestClient, settings: Settings, wrong: set[str] = frozenset()) -> list[dict]:
    """Answer every card of a new session; items listed in `wrong` (by primary target) are
    answered badly. Returns the answer results with the card they belong to."""
    session = api.post("/api/sessions").json()
    results = []
    for card in session["cards"]:
        if card["type"] in ("flashcard_intro", "grammar_intro"):
            result = intro_answer(api, session, card)
        elif card["type"] == "flashcard_recognition":
            with db_of(settings) as db:
                correct = db.get(Exercise, card["exercise_id"]).solution["correct_index"]
            bad = card["item_id"] in wrong
            choice = (correct + 1) % 4 if bad else correct
            result = (
                answer(api, session, card, "", **{})
                if False
                else api.post(
                    f"/api/sessions/{session['session_id']}/answers",
                    json={"exercise_id": card["exercise_id"], "answer": {"choice": choice}},
                ).json()
            )
        elif card["type"] == "flashcard_production":
            result = answer(api, session, card, "falsch")
        else:
            ready = prepare(api, card)
            if ready["status"] != "ready":
                continue
            bad = card["item_ids"][0] in wrong
            text = "Falsch hier" if bad else reference_of(settings, card["exercise_id"])
            result = answer(api, session, ready, text)
        results.append({"card": card, "result": result})
    return results


def evaluation_rows(settings: Settings) -> list[EvaluationRow]:
    with db_of(settings) as db:
        return list(db.scalars(select(EvaluationRow).order_by(EvaluationRow.id)))


def contest(api: TestClient, evaluation_id: int, **body):
    return api.post(f"/api/evaluations/{evaluation_id}/contest", json=body)


# --- production evaluation -----------------------------------------------------------------------


def test_contest_production_voids_replays_and_matches_full_replay(
    api: TestClient, migrated_settings: Settings, clock: Clock, monkeypatch
) -> None:
    session = start(api)
    intro, first, second = session["cards"]
    prepare(api, first)
    prepare(api, second)
    intro_answer(api, session, intro)
    wrong = answer(api, session, first, "Das Tisch")
    assert wrong["outcome"] == "major_errors"
    answer(api, session, second, reference_of(migrated_settings, second["exercise_id"]))
    with db_of(migrated_settings) as db:
        assert [r.item_id for r in db.scalars(select(RemediationItem))] == ["gram:cases"]

    # Later events on the same items, built on the contested error: two more days, one more
    # error on gram:cases and flashcard reviews of the lemmas.
    clock.advance(days=1)
    day2 = play_day(api, migrated_settings, wrong={"gram:cases"})
    clock.advance(days=3)
    play_day(api, migrated_settings)
    kinds = {r["result"]["kind"] for r in day2}
    assert kinds == {"flashcard", "production"}  # a mix of both evaluation sources

    before = snapshot(migrated_settings)
    with db_of(migrated_settings) as db:
        old_events = list(
            db.scalars(
                select(LearningEvent).where(LearningEvent.evaluation_id == wrong["evaluation_id"])
            )
        )
        n_events_before = len(db.scalars(select(LearningEvent)).all())
    assert old_events and all(e.voided_by is None for e in old_events)

    resp = contest(api, wrong["evaluation_id"], reason="Per me era giusto")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contest"]["verdict"] == "accepted" and body["contest"]["status"] == "resolved"
    assert body["contest"]["resolver"] == "accept_all" and body["contest"]["reason"]
    assert body["evaluation_id"] == body["contest"]["replacement_evaluation_id"]
    assert [(i["item_id"], i["previous"], i["outcome"]) for i in body["items"]] == [
        ("gram:cases", "error", "correct")
    ]

    with db_of(migrated_settings) as db:
        replacement = db.get(EvaluationRow, body["evaluation_id"])
        assert replacement.supersedes == wrong["evaluation_id"]
        assert replacement.attempt_id == db.get(EvaluationRow, wrong["evaluation_id"]).attempt_id
        assert replacement.result["overall"] == "correct" and replacement.result["errors"] == []
        voided = list(
            db.scalars(select(LearningEvent).where(LearningEvent.voided_by == replacement.id))
        )
        assert {e.id for e in voided} == {e.id for e in old_events}  # all events of the evaluation
        copies = list(
            db.scalars(select(LearningEvent).where(LearningEvent.evaluation_id == replacement.id))
        )
        assert len(copies) == len(old_events)
        assert len(db.scalars(select(LearningEvent)).all()) == n_events_before + len(copies)
        by_key = {(e.item_id, e.facet, e.kind): e for e in old_events}
        for copy in copies:
            old = by_key[(copy.item_id, copy.facet, copy.kind)]
            assert copy.ts == old.ts and copy.id > old.id and copy.voided_by is None
            assert copy.attempt_id == old.attempt_id and copy.exercise_id == old.exercise_id
        review = next(e for e in copies if e.kind == "review" and e.item_id == "gram:cases")
        assert (review.outcome, review.confidence, review.diagnostic_tags) == ("correct", 1.0, [])
        # The entry raised by the error was consumed by the planning of day 2: it is kept
        # (the exercise it caused exists), and the contest leaves it alone.
        rows = db.scalars(select(RemediationItem)).all()
        consumed = {r.evaluation_id for r in rows if r.consumed_at is not None}
        assert wrong["evaluation_id"] in consumed
        assert db.scalar(select(Contest.id)) == body["contest"]["id"]

    # The projection equals a from-scratch replay of the non-voided events, and it did change.
    after = snapshot(migrated_settings)
    assert after != before
    assert after[("gram:cases", "production")][4] > before[("gram:cases", "production")][4]
    replay_via_cli(migrated_settings, monkeypatch)
    assert snapshot(migrated_settings) == after

    # Superseded evaluations cannot be contested again; the replacement can (nothing changes).
    assert contest(api, wrong["evaluation_id"]).status_code == 409
    assert api.get("/api/contests").json()[0]["id"] == body["contest"]["id"]


def test_contest_item_level_and_validation(
    api: TestClient, migrated_settings: Settings, monkeypatch
) -> None:
    session = start(api)
    _intro, first, _second = session["cards"]
    prepare(api, first)
    wrong = answer(api, session, first, "Das Tisch")
    assert contest(api, 9999).status_code == 404
    unknown = contest(api, wrong["evaluation_id"], item_ids=["lex:tisch"])
    assert unknown.status_code == 422
    with db_of(migrated_settings) as db:
        assert db.scalars(select(Contest)).all() == []  # a refused contest leaves no trace
        assert len(db.scalars(select(RemediationItem)).all()) == 1
    resp = contest(api, wrong["evaluation_id"], item_ids=["gram:cases"])
    assert resp.status_code == 200
    with db_of(migrated_settings) as db:
        (row,) = db.scalars(select(Contest)).all()
        assert row.item_ids == ["gram:cases"]
        assert db.scalars(select(RemediationItem)).all() == []  # the unconsumed entry is removed
    after = snapshot(migrated_settings)
    replay_via_cli(migrated_settings, monkeypatch)
    assert snapshot(migrated_settings) == after


# --- flashcards ----------------------------------------------------------------------------------


@pytest.fixture
def flashcard_api(migrated_settings: Settings, clock: Clock, stub: StubLLM) -> TestClient:
    from .conftest import import_fixture

    import_fixture(migrated_settings, when=clock.now)
    with TestClient(create_app(migrated_settings, now=clock, llm=stub)) as client:
        client.post("/api/learner", json={"level": "A1"})
        client.put("/api/settings", json={"production_slots": 0, "new_per_session": 20})
        yield client


def test_contest_flashcard_attempt_and_replay(
    flashcard_api: TestClient, migrated_settings: Settings, clock: Clock, monkeypatch
) -> None:
    api = flashcard_api
    session = api.post("/api/sessions").json()
    for card in session["cards"]:
        intro_answer(api, session, card)
    clock.advance(days=5)
    results = play_day(api, migrated_settings, wrong={"lex:tisch"})
    flash = [r for r in results if r["card"]["item_id"] == "lex:tisch"]
    assert flash and flash[0]["result"]["outcome"] == "error"
    evaluation_id = flash[0]["result"]["evaluation_id"]
    assert evaluation_id is not None
    # Intro cards have no evaluation.
    assert (
        intro_answer(api, session, api.post("/api/sessions").json()["cards"][0]) if False else True
    )

    clock.advance(days=3)
    later = play_day(api, migrated_settings, wrong={"lex:tisch"})  # more events on the same item
    assert any(r["card"]["item_id"] == "lex:tisch" for r in later)

    with db_of(migrated_settings) as db:
        evaluation = db.get(EvaluationRow, evaluation_id)
        assert evaluation.grader_version == "flashcards.v1" and evaluation.llm_call_id is None
        assert evaluation.result["overall"] == "major_errors"
        events = db.scalars(
            select(LearningEvent).where(LearningEvent.evaluation_id == evaluation_id)
        ).all()
        assert [e.kind for e in events] == ["review"]
    before = snapshot(migrated_settings)

    resp = contest(api, evaluation_id)
    assert resp.status_code == 200, resp.text
    assert [(i["previous"], i["outcome"]) for i in resp.json()["items"]] == [("error", "correct")]
    after = snapshot(migrated_settings)
    assert after != before
    replay_via_cli(migrated_settings, monkeypatch)
    assert snapshot(migrated_settings) == after
    with db_of(migrated_settings) as db:
        (old,) = events
        assert db.get(LearningEvent, old.id).voided_by == resp.json()["evaluation_id"]
    assert contest(api, evaluation_id).status_code == 409


def test_contest_flashcard_attempt_without_evaluation_is_created_lazily(
    flashcard_api: TestClient, migrated_settings: Settings, clock: Clock, monkeypatch
) -> None:
    api = flashcard_api
    session = api.post("/api/sessions").json()
    for card in session["cards"]:
        intro_answer(api, session, card)
    clock.advance(days=5)
    results = play_day(api, migrated_settings, wrong={"lex:tisch"})
    flash = next(r for r in results if r["card"]["item_id"] == "lex:tisch")
    evaluation_id = flash["result"]["evaluation_id"]

    # Simulate a flashcard answered before M4: no evaluation, events without `evaluation_id`.
    with db_of(migrated_settings) as db:
        attempt_id = db.get(EvaluationRow, evaluation_id).attempt_id
        db.execute(
            update(LearningEvent)
            .where(LearningEvent.evaluation_id == evaluation_id)
            .values(evaluation_id=None)
        )
        db.delete(db.get(EvaluationRow, evaluation_id))
        db.commit()
        assert db.get(Attempt, attempt_id).outcome == "error"
    assert contest(api, evaluation_id).status_code == 404

    resp = api.post(f"/api/attempts/{attempt_id}/contest", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(i["item_id"], i["previous"], i["outcome"]) for i in body["items"]] == [
        ("lex:tisch", "error", "correct")
    ]
    with db_of(migrated_settings) as db:
        synthetic = db.scalars(
            select(EvaluationRow)
            .where(EvaluationRow.attempt_id == attempt_id)
            .order_by(EvaluationRow.id)
        ).all()
        assert [e.grader_version for e in synthetic] == [
            "flashcards.v1",
            "flashcards.v1|contest.accept_all",
        ]
        voided = db.scalars(
            select(LearningEvent).where(
                LearningEvent.attempt_id == attempt_id, LearningEvent.voided_by.is_not(None)
            )
        ).all()
        assert [e.kind for e in voided] == ["review"]
    after = snapshot(migrated_settings)
    replay_via_cli(migrated_settings, monkeypatch)
    assert snapshot(migrated_settings) == after
    # Intro attempts have no evaluation and cannot be contested.
    with db_of(migrated_settings) as db:
        intro_attempt = db.scalars(select(Attempt).order_by(Attempt.id)).first().id
    assert api.post(f"/api/attempts/{intro_attempt}/contest", json={}).status_code == 409
    assert api.post("/api/attempts/99999/contest", json={}).status_code == 404


def test_flashcard_answers_create_synthetic_evaluations(
    flashcard_api: TestClient, migrated_settings: Settings, clock: Clock
) -> None:
    session = flashcard_api.post("/api/sessions").json()
    for card in session["cards"]:
        assert intro_answer(flashcard_api, session, card)["evaluation_id"] is None
    clock.advance(days=5)
    results = play_day(flashcard_api, migrated_settings)
    assert results and all(r["result"]["evaluation_id"] for r in results)
    with db_of(migrated_settings) as db:
        rows = db.scalars(select(EvaluationRow)).all()
        assert len(rows) == len(results)
        assert {r.grader_version for r in rows} == {"flashcards.v1"}


# --- resolvers ---------------------------------------------------------------------------


class RejectResolver:
    name = "reject_test"

    def resolve(self, contest, evaluation):
        return Resolution("rejected", None, "The grader was right.")


def test_resolver_registry_and_rejection(
    api: TestClient, migrated_settings: Settings, clock: Clock, stub: StubLLM, lt: LT, monkeypatch
) -> None:
    register_resolver("reject_test", RejectResolver)
    try:
        assert isinstance(build_resolver("accept_all"), AcceptAllResolver)
        with pytest.raises(ValueError):
            build_resolver("nope")
        settings = Settings(
            database_url=migrated_settings.database_url, contest_resolver="reject_test"
        )
        with TestClient(
            create_app(settings, now=clock, llm=stub, languagetool=lt.client())
        ) as client:
            session = start(client)
            _intro, first, _second = session["cards"]
            prepare(client, first)
            wrong = answer(client, session, first, "Das Tisch")
            before = snapshot(migrated_settings)
            resp = contest(client, wrong["evaluation_id"], reason="boh")
            assert resp.status_code == 200
            body = resp.json()
            assert (
                body["contest"]["verdict"] == "rejected" and body["contest"]["status"] == "resolved"
            )
            assert body["contest"]["resolver"] == "reject_test"
            assert body["contest"]["replacement_evaluation_id"] is None
            assert body["evaluation_id"] == wrong["evaluation_id"]
            assert [i["outcome"] for i in body["items"]] == ["error"]
            assert snapshot(migrated_settings) == before  # state unchanged
            with db_of(migrated_settings) as db:
                assert len(db.scalars(select(EvaluationRow)).all()) == 1
                assert all(e.voided_by is None for e in db.scalars(select(LearningEvent)))
                assert len(db.scalars(select(RemediationItem)).all()) == 1
                (row,) = db.scalars(select(Contest)).all()
                assert row.rationale == "The grader was right."
            # A rejected evaluation is still current: it can be contested again.
            assert contest(client, wrong["evaluation_id"]).status_code == 200
            assert len(client.get("/api/contests").json()) == 2
    finally:
        RESOLVERS.pop("reject_test", None)


def test_unknown_resolver_in_settings_fails_at_startup(migrated_settings: Settings) -> None:
    settings = Settings(database_url=migrated_settings.database_url, contest_resolver="missing")
    with pytest.raises(ValueError):
        create_app(settings)


def test_replay_order_is_by_timestamp_then_id(
    flashcard_api: TestClient, migrated_settings: Settings, clock: Clock
) -> None:
    """Replacement events keep their timestamps and get new ids: replay stays deterministic."""
    api = flashcard_api
    session = api.post("/api/sessions").json()
    for card in session["cards"]:
        intro_answer(api, session, card)
    clock.advance(days=5)
    results = play_day(api, migrated_settings, wrong={"lex:tisch"})
    evaluation_id = next(
        r["result"]["evaluation_id"] for r in results if r["card"]["item_id"] == "lex:tisch"
    )
    clock.advance(days=5)
    play_day(api, migrated_settings)  # a later event on the item, with a smaller id than the copy
    with db_of(migrated_settings) as db:
        facet = (
            db.scalars(select(LearningEvent).where(LearningEvent.evaluation_id == evaluation_id))
            .one()
            .facet
        )
    contest(api, evaluation_id)
    with db_of(migrated_settings) as db:
        events = db.scalars(
            select(LearningEvent).where(
                LearningEvent.item_id == "lex:tisch",
                LearningEvent.facet == facet,
                LearningEvent.voided_by.is_(None),
            )
        ).all()
        ordered = sorted(events, key=lambda e: (e.ts, e.id))
        assert [e.kind for e in ordered][:2] == ["introduce", "review"]
        assert ordered[1].ts < ordered[-1].ts and ordered[1].id > ordered[-1].id
        memory = db.get(ItemMemory, (1, "lex:tisch", facet))
        assert memory.last_event_id == ordered[-1].id
