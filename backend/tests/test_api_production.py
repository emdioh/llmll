import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import main as cli_main
from app.config import Settings, get_settings
from app.llm.client import LLMError, LLMUnavailable
from app.llm.fake import FakeLLMClient
from app.llm.types import GeneratedExercise, GradeResult
from app.main import create_app
from app.nlp.languagetool import LanguageToolClient
from app.store.db import create_session_factory
from app.store.models import (
    Attempt,
    Evaluation,
    Exercise,
    ItemMemory,
    LearnerItem,
    LearningEvent,
    LLMCall,
    RemediationItem,
    StoredExplanation,
)

from .conftest import Clock, import_fixture
from .test_api_flow import snapshot


class StubLLM(FakeLLMClient):
    """The fake LLM with failure and extra-output hooks."""

    def __init__(self, recorder=None) -> None:
        super().__init__(recorder)
        self.generate_calls = 0
        self.fail_grade: Exception | None = None
        self.fail_generate: Exception | None = None
        self.empty_glossary = False
        self.extra_correct_uses: list[str] = []

    def generate_exercise(self, req):
        self.generate_calls += 1
        if self.fail_generate:
            raise self.fail_generate
        result = super().generate_exercise(req)
        if self.empty_glossary:
            return GeneratedExercise(**{**result.model_dump(), "glossary": []})
        return result

    def grade_sentence(self, req):
        if self.fail_grade:
            raise self.fail_grade
        result = super().grade_sentence(req)
        if self.extra_correct_uses:
            result = GradeResult(
                **{
                    **result.model_dump(),
                    "correct_uses": [*result.correct_uses, *self.extra_correct_uses],
                }
            )
        return result


class LT:
    """Controllable LanguageTool transport."""

    def __init__(self) -> None:
        self.matches: list[dict] = []
        self.down = False
        self.requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.down:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"matches": self.matches})

    def client(self) -> LanguageToolClient:
        return LanguageToolClient("http://lt", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def lt() -> LT:
    return LT()


@pytest.fixture
def stub(migrated_settings: Settings, clock: Clock) -> StubLLM:
    from app.llm.calls import SqlCallRecorder

    return StubLLM(SqlCallRecorder(create_session_factory(migrated_settings), clock))


@pytest.fixture
def api(migrated_settings: Settings, clock: Clock, stub: StubLLM, lt: LT) -> TestClient:
    import_fixture(migrated_settings, when=clock.now)
    app = create_app(migrated_settings, now=clock, llm=stub, languagetool=lt.client())
    with TestClient(app) as client:
        yield client


def start(api: TestClient, level: str = "A2") -> dict:
    assert api.post("/api/learner", json={"level": level}).status_code == 201
    resp = api.post("/api/sessions")
    assert resp.status_code == 201, resp.text
    return resp.json()


def answer(api: TestClient, session: dict, card: dict, text: str, **kw) -> dict:
    resp = api.post(
        f"/api/sessions/{session['session_id']}/answers",
        json={"exercise_id": card["exercise_id"], "answer": {"text": text}, **kw},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def intro_answer(api: TestClient, session: dict, card: dict) -> dict:
    resp = api.post(
        f"/api/sessions/{session['session_id']}/answers", json={"exercise_id": card["exercise_id"]}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def events_of(settings: Settings, exercise_id: str) -> list[LearningEvent]:
    with create_session_factory(settings)() as db:
        return list(
            db.scalars(
                select(LearningEvent)
                .where(LearningEvent.exercise_id == exercise_id)
                .order_by(LearningEvent.id)
            )
        )


def test_health_reports_fake_llm(api: TestClient) -> None:
    assert api.get("/api/health").json()["llm"] == "fake"


def test_session_has_production_slots_and_grammar_intro(api: TestClient) -> None:
    session = start(api)
    types = [c["type"] for c in session["cards"]]
    assert types == ["grammar_intro", "production", "production"]
    intro, first, second = session["cards"]
    assert intro["item_id"] == "gram:cases"
    assert intro["prompt"]["title"] == "Casi" and intro["prompt"]["examples"]
    for card in (first, second):
        assert card["status"] == "pending" and card["prompt"]["text"] is None
        assert card["subtype"] and card["item_ids"]
    assert first["item_ids"][0] == "gram:cases"
    assert first["subtype"] == "translation"  # new grammar point
    # new lemmas appear in exercises, not as flashcard intros; budget: new_per_session 5
    new_lemmas = [i for c in (first, second) for i in c["item_ids"] if i.startswith("lex:")]
    assert len(new_lemmas) == len(set(new_lemmas)) == 4  # all A2 candidates of the fixture
    assert api.get("/api/settings").json()["production_slots"] == 2


def test_prepare_is_idempotent_and_hides_solutions(api: TestClient, stub: StubLLM) -> None:
    session = start(api)
    card = session["cards"][1]
    resp = api.post(f"/api/exercises/{card['exercise_id']}/prepare")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready" and body["type"] == "production"
    assert body["prompt"]["text"] == "Il tavolo." and body["instructions"]
    glossary_ids = {g["item_id"] for g in body["glossary"]}
    assert glossary_ids == {i for i in card["item_ids"] if i.startswith("lex:")}
    assert "reference_solutions" not in resp.text and "Der Tisch." not in resp.text
    again = api.post(f"/api/exercises/{card['exercise_id']}/prepare").json()
    assert again == body and stub.generate_calls == 1
    assert api.post("/api/exercises/nope/prepare").status_code == 404
    with create_session_factory(api.app.state.settings)() as db:
        call = db.scalars(select(LLMCall).where(LLMCall.task == "generate_exercise")).one()
        assert call.model == "fake"


def test_answering_before_prepare_is_rejected(api: TestClient) -> None:
    session = start(api)
    card = session["cards"][1]
    resp = api.post(
        f"/api/sessions/{session['session_id']}/answers",
        json={"exercise_id": card["exercise_id"], "answer": {"text": "x"}},
    )
    assert resp.status_code == 409


def test_generation_failure_falls_back_to_flashcards(api: TestClient, stub: StubLLM) -> None:
    stub.empty_glossary = True  # new lemmas missing from the glossary: invalid, retried once
    session = start(api)
    card = session["cards"][1]
    body = api.post(f"/api/exercises/{card['exercise_id']}/prepare").json()
    assert stub.generate_calls == 2
    assert body["status"] == "failed" and body["prompt"]["text"] is None
    fallback = body["fallback_cards"]
    assert fallback and {c["type"] for c in fallback} == {"flashcard_intro"}
    assert {c["item_id"] for c in fallback} == {i for i in card["item_ids"] if i.startswith("lex:")}
    # idempotent: the same fallback cards come back, and no further LLM call is made
    again = api.post(f"/api/exercises/{card['exercise_id']}/prepare").json()
    assert again["fallback_cards"] == fallback and stub.generate_calls == 2
    # the fallback intros work as M1 intro cards; the failed exercise cannot be answered
    intro_answer(api, session, fallback[0])
    assert (
        api.post(
            f"/api/sessions/{session['session_id']}/answers",
            json={"exercise_id": card["exercise_id"], "answer": {"text": "x"}},
        ).status_code
        == 409
    )


def test_generation_llm_error_marks_failed(api: TestClient, stub: StubLLM) -> None:
    stub.fail_generate = LLMError("boom")
    session = start(api)
    body = api.post(f"/api/exercises/{session['cards'][1]['exercise_id']}/prepare").json()
    assert body["status"] == "failed" and stub.generate_calls == 1


def test_full_production_flow(
    api: TestClient, migrated_settings: Settings, clock: Clock, stub: StubLLM, lt: LT, monkeypatch
) -> None:
    session = start(api)
    intro, first, second = session["cards"]
    for card in (first, second):
        assert api.post(f"/api/exercises/{card['exercise_id']}/prepare").json()["status"] == "ready"

    # Grammar intro: introduce event on the production facet.
    result = intro_answer(api, session, intro)
    assert result["expected"] is None and result["memory"]["facet"] == "production"
    with create_session_factory(migrated_settings)() as db:
        assert db.get(LearnerItem, (1, "gram:cases")).status == "introduced"

    # Wrong answer; LanguageTool agrees (match overlaps the answer).
    lt.matches = [
        {"offset": 0, "length": 3, "message": "m", "rule": {"id": "R", "category": {"id": "G"}}}
    ]
    wrong = answer(api, session, first, "Das Tisch")
    assert wrong["kind"] == "production" and wrong["outcome"] == "major_errors"
    assert wrong["corrected_sentence"] == "Der Tisch."
    (error,) = wrong["errors"]
    assert (error["start"], error["end"], error["item_id"]) == (0, 9, "gram:cases")
    assert error["confidence"] == 0.9
    items = {i["item_id"]: i for i in wrong["items"]}
    assert items["gram:cases"]["outcome"] == "error" and items["gram:cases"]["needs_remediation"]
    assert items["gram:cases"]["label"] == "Casi"
    assert set(items) == {"gram:cases"}  # new lemmas have no evidence in a failed answer

    events = events_of(migrated_settings, first["exercise_id"])
    kinds = [(e.kind, e.item_id, e.facet) for e in events]
    new_lemmas = [i for i in first["item_ids"] if i.startswith("lex:")]
    for lemma in new_lemmas:  # introduced by being used in the exercise
        assert ("introduce", lemma, "recognition") in kinds
        assert ("introduce", lemma, "production") in kinds
    review = next(e for e in events if e.kind == "review")
    assert (review.item_id, review.outcome, review.confidence) == ("gram:cases", "error", 0.9)
    assert review.evaluation_id == wrong["evaluation_id"] and review.attempt_id
    assert abs(review.evidence_weight - 1.0) < 1e-9  # translation weight x primary weight 1.0

    with create_session_factory(migrated_settings)() as db:
        evaluation = db.get(Evaluation, wrong["evaluation_id"])
        assert evaluation.grader_version.startswith("fake-v1|fake|r1-")
        assert evaluation.lt_matches[0]["rule_id"] == "R" and evaluation.llm_call_id
        assert evaluation.result["feedback"]
        queue = db.scalars(select(RemediationItem)).all()
        assert [(q.item_id, q.consumed_at, q.evaluation_id) for q in queue] == [
            ("gram:cases", None, evaluation.id)
        ]

    # Explanation: generated once, then cached.
    explain = api.post(
        f"/api/evaluations/{wrong['evaluation_id']}/explain", json={"item_id": "gram:cases"}
    )
    assert explain.status_code == 200 and explain.json()["cached"] is False
    assert explain.json()["markdown"] == "In tedesco: *der*, *die*, *das*."
    assert api.post(
        f"/api/evaluations/{wrong['evaluation_id']}/explain", json={"item_id": "gram:cases"}
    ).json()["cached"]
    assert (
        api.post(
            f"/api/evaluations/{wrong['evaluation_id']}/explain", json={"item_id": "lex:tisch"}
        ).status_code
        == 404
    )
    assert (
        api.post("/api/evaluations/999/explain", json={"item_id": "gram:cases"}).status_code == 404
    )
    with create_session_factory(migrated_settings)() as db:
        assert len(db.scalars(select(StoredExplanation)).all()) == 1
        explain_calls = db.scalars(select(LLMCall).where(LLMCall.task == "explain")).all()
        assert len(explain_calls) == 1

    # On-demand grammar question.
    asked = api.post("/api/grammar/gram:cases/explain", json={"question": "Quando uso il dativo?"})
    assert asked.status_code == 200 and asked.json()["examples"]
    assert api.post("/api/grammar/lex:tisch/explain", json={"question": "?"}).status_code == 404
    assert api.post("/api/grammar/gram:cases/explain", json={"question": ""}).status_code == 422

    stub.extra_correct_uses = ["lex:tisch"]  # a known, non-target item used correctly
    # Correct answer on the second exercise: introduce + review (targets) + implicit (non-target).
    gen = api.post(f"/api/exercises/{second['exercise_id']}/prepare").json()
    with create_session_factory(migrated_settings)() as db:
        reference = db.get(Exercise, second["exercise_id"]).solution["reference_solutions"][0]
    right = answer(api, session, gen, reference)
    assert right["outcome"] == "correct" and right["errors"] == []
    right_items = {i["item_id"]: i["outcome"] for i in right["items"]}
    assert right_items["lex:tisch"] == "correct"
    assert all(v == "correct" for v in right_items.values())
    events = events_of(migrated_settings, second["exercise_id"])
    by_item = {(e.item_id, e.kind) for e in events}
    assert ("lex:tisch", "implicit") in by_item and ("lex:tisch", "status_change") in by_item
    implicit = next(e for e in events if e.kind == "implicit")
    assert abs(implicit.evidence_weight - 0.6) < 1e-9 and implicit.presumed_known
    for item_id in gen["item_ids"]:
        assert (item_id, "review") in by_item
    with create_session_factory(migrated_settings)() as db:
        assert db.get(LearnerItem, (1, "lex:tisch")).status == "introduced"
        assert len(db.scalars(select(Attempt)).all()) == 3  # intro + 2 productions

    # Re-answering is rejected; the remediation item feeds the next session.
    assert (
        api.post(
            f"/api/sessions/{session['session_id']}/answers",
            json={"exercise_id": first["exercise_id"], "answer": {"text": "x"}},
        ).status_code
        == 409
    )
    nxt = api.post("/api/sessions").json()
    productions = [c for c in nxt["cards"] if c["type"] == "production"]
    assert productions[0]["item_ids"][0] == "gram:cases"
    intros = [c["item_id"] for c in nxt["cards"] if c["type"] == "grammar_intro"]
    assert "gram:cases" not in intros  # already introduced; the construction it unlocks is new
    assert intros == ["cx:lust-haben-auf"]
    with create_session_factory(migrated_settings)() as db:
        (row,) = db.scalars(select(RemediationItem)).all()
        assert row.consumed_at is not None
        exercise = db.get(Exercise, productions[0]["exercise_id"])
        assert exercise.targets[0]["role"] == "primary"

    # Replay reproduces the incrementally built projection exactly.
    before = snapshot(migrated_settings)
    assert before
    monkeypatch.setenv("LLMLL_DATABASE_URL", migrated_settings.database_url)
    get_settings.cache_clear()
    try:
        assert cli_main(["replay"]) == 0
    finally:
        get_settings.cache_clear()
    assert snapshot(migrated_settings) == before
    with create_session_factory(migrated_settings)() as db:
        assert db.scalar(select(ItemMemory).where(ItemMemory.item_id == "gram:cases")) is not None


def test_languagetool_down_does_not_block_grading(
    api: TestClient, migrated_settings: Settings, lt: LT
) -> None:
    lt.down = True
    session = start(api)
    intro, first, _ = session["cards"]
    intro_answer(api, session, intro)
    api.post(f"/api/exercises/{first['exercise_id']}/prepare")
    result = answer(api, session, first, "Das Tisch")
    assert result["outcome"] == "major_errors"
    with create_session_factory(migrated_settings)() as db:
        evaluation = db.get(Evaluation, result["evaluation_id"])
        assert evaluation.lt_matches is None
        assert evaluation.result["lt_available"] is False
        assert evaluation.result["errors"][0]["confidence"] == 0.9  # not dampened


def test_grading_failure_leaves_no_attempt(
    api: TestClient, migrated_settings: Settings, stub: StubLLM
) -> None:
    session = start(api)
    intro, first, _ = session["cards"]
    api.post(f"/api/exercises/{first['exercise_id']}/prepare")
    url = f"/api/sessions/{session['session_id']}/answers"
    body = {"exercise_id": first["exercise_id"], "answer": {"text": "Der Tisch."}}
    stub.fail_grade = LLMUnavailable("down")
    assert api.post(url, json=body).status_code == 503
    stub.fail_grade = LLMError("bad")
    assert api.post(url, json=body).status_code == 502
    with create_session_factory(migrated_settings)() as db:
        assert db.scalars(select(Attempt)).all() == []
    stub.fail_grade = None  # the learner can simply retry
    assert api.post(url, json=body).json()["outcome"] == "correct"


def test_production_slots_zero_keeps_flashcard_mode(api: TestClient) -> None:
    api.post("/api/learner", json={"level": "A1"})
    api.put("/api/settings", json={"production_slots": 0})
    cards = api.post("/api/sessions").json()["cards"]
    assert cards and {c["type"] for c in cards} == {"flashcard_intro"}
    assert api.put("/api/settings", json={"production_slots": 6}).status_code == 422
