"""Seeding of a multi-day learning history through the real API (shared by the M8 tests)."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.types import GradeResult
from app.main import create_app
from app.nlp.analyzer import FakeAnalyzer
from app.store.db import create_session_factory
from app.store.models import Exercise

from .conftest import Clock, import_fixture
from .test_api_production import LT, StubLLM
from .test_contests import play_day, prepare, reference_of
from .test_reading import SOURCE

TZ = "Europe/Rome"


class TaggingLLM(StubLLM):
    """The stub LLM whose errors carry a diagnostic tag of the grammar point (`m`)."""

    def grade_sentence(self, req):
        result = super().grade_sentence(req)
        errors = [
            e.model_copy(update={"diagnostic_tags": ["m"] if e.item_id == "gram:cases" else []})
            for e in result.errors
        ]
        return GradeResult(**{**result.model_dump(), "errors": errors})


def make_client(settings: Settings, clock: Clock, llm: StubLLM, lt: LT) -> TestClient:
    import_fixture(settings, when=clock.now)
    app = create_app(
        settings, now=clock, llm=llm, languagetool=lt.client(), analyzer=FakeAnalyzer()
    )
    return TestClient(app)


def post_answer(api: TestClient, session_id: str, card: dict, **body) -> dict:
    resp = api.post(
        f"/api/sessions/{session_id}/answers", json={"exercise_id": card["exercise_id"], **body}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def seed_history(api: TestClient, settings: Settings, clock: Clock) -> dict:
    """Placement (Oct 5), a written-exercise session with an error (Oct 6), another day with
    an error (Oct 7), nothing on Oct 8, flashcards + a reading (Oct 9). Leaves the clock on
    Oct 10 09:00 UTC, with nothing studied yet that day. Nothing is contested yet."""
    ids: dict = {}
    assert api.post("/api/learner", json={"level": "A2"}).status_code == 201
    assert api.put("/api/settings", json={"timezone": TZ}).status_code == 200

    # Oct 5: the placement test (its events count for item state, not for streaks).
    placement = api.post("/api/placement").json()
    ids["placement_id"] = placement["placement_id"]
    vocab = [c for c in placement["cards"] if c["type"] == "flashcard_recognition"][:3]
    ids["placement_items"] = [c["item_id"] for c in vocab]
    for n, card in enumerate(vocab):
        with create_session_factory(settings)() as db:
            correct = db.get(Exercise, card["exercise_id"]).solution["correct_index"]
        choice = correct if n else (correct + 1) % 4
        post_answer(api, placement["placement_id"], card, answer={"choice": choice})

    # Oct 6: a grammar introduction, a wrong and a right written exercise (with durations).
    clock.advance(days=1)
    session = api.post("/api/sessions").json()
    ids["session1"] = session["session_id"]
    first_production = True
    for card in session["cards"]:
        if card["type"] in ("grammar_intro", "flashcard_intro"):
            post_answer(api, session["session_id"], card, duration_ms=4000)
            continue
        if card["type"] == "flashcard_recognition":
            with create_session_factory(settings)() as db:
                correct = db.get(Exercise, card["exercise_id"]).solution["correct_index"]
            post_answer(
                api, session["session_id"], card, answer={"choice": correct}, duration_ms=3000
            )
            continue
        if card["type"] == "flashcard_production":
            post_answer(api, session["session_id"], card, answer={"text": "falsch"})
            continue
        ready = prepare(api, card)
        if first_production:
            first_production = False
            text, duration = "Das Tisch", 20000
        else:
            text, duration = reference_of(settings, card["exercise_id"]), 30000
        result = post_answer(
            api,
            session["session_id"],
            ready,
            answer={"text": text},
            duration_ms=duration,
        )
        if text == "Das Tisch":
            ids["wrong_evaluation"] = result["evaluation_id"]
            ids["wrong_attempt"] = result["attempt_id"]
            ids["wrong_exercise"] = card["exercise_id"]

    # Oct 7: the remediation item comes first and is answered badly again. Oct 8: nothing.
    clock.advance(days=1)
    play_day(api, settings, wrong={"gram:cases"})

    # Oct 9: flashcards and written exercises, then a reading with a lookup and its summary.
    clock.advance(days=2)
    play_day(api, settings)
    text = api.post("/api/texts", json={"text": SOURCE}).json()
    reading = api.post(f"/api/texts/{text['id']}/reading").json()
    body = text["version"]["body"]
    tisch = next(t for t in text["version"]["tokens"] if body[t["start"] : t["end"]] == "Tisch")
    gloss = api.post(f"/api/reading/{reading['id']}/gloss", json={"token_index": tisch["i"]})
    assert gloss.status_code == 200, gloss.text
    clock.advance(minutes=10)
    finished = api.post(f"/api/reading/{reading['id']}/finish").json()
    clock.advance(minutes=2)
    post_answer(
        api,
        finished["session_id"],
        finished["exercise"],
        answer={"text": "Der Tisch ist im Haus."},
    )
    ids["reading_id"] = reading["id"]
    ids["text_title"] = text["version"]["title"]

    clock.advance(days=1)
    return ids


@pytest.fixture
def llm(migrated_settings: Settings, clock: Clock) -> TaggingLLM:
    from app.llm.calls import SqlCallRecorder

    return TaggingLLM(SqlCallRecorder(create_session_factory(migrated_settings), clock))


@pytest.fixture
def history_api(migrated_settings: Settings, clock: Clock, llm: TaggingLLM, lt: LT):
    with make_client(migrated_settings, clock, llm, lt) as client:
        yield client
