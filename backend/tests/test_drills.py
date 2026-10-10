# ruff: noqa: F811
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.domain.answers import check_gap
from app.domain.drills import plan_drill
from app.store.db import create_session_factory
from app.store.models import Evaluation, Exercise, LearningEvent, LLMCall

from .test_api_production import (  # noqa: F401
    LT,
    StubLLM,
    api,
    lt,
    stub,
)

TAGS = ["m", "f", "n"]


def subtypes(size: int, tags: list[str] = TAGS) -> list[str]:
    return [step.subtype for step in plan_drill(size, tags)]


# --- pure planning and gap checking --------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (3, ["choice", "cloze", "translation"]),
        (6, ["choice", "choice", "cloze", "cloze", "translation", "transform"]),
        (
            10,
            ["choice"] * 3 + ["cloze"] * 4 + ["translation", "transform", "guided"],
        ),
    ],
)
def test_plan_drill_order_and_length(size: int, expected: list[str]) -> None:
    assert subtypes(size) == expected


def test_plan_drill_focus_tags_rotate() -> None:
    steps = plan_drill(6, TAGS)
    assert [s.focus_tags for s in steps] == [("m",), ("f",), ("n",), ("m",), ("f",), ("n",)]


def test_plan_drill_without_tags_has_no_focus() -> None:
    assert all(step.focus_tags == () for step in plan_drill(6, []))


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("dem", "correct"),
        ("Dem", "correct"),
        ("dem.", "correct"),
        (" dem! ", "correct"),
        ("den", "error"),
        ("das", "error"),
    ],
)
def test_check_gap(answer: str, expected: str) -> None:
    assert check_gap(answer, ["dem"]) == expected


def test_check_gap_umlaut_written_in_ascii_is_assisted() -> None:
    assert check_gap("fuer", ["für"]) == "assisted"
    assert check_gap("für", ["für"]) == "correct"
    assert check_gap("fur", ["für"]) == "error"


# --- helpers -------------------------------------------------------------------------------------


def new_drill(api: TestClient, item_id: str = "gram:cases", level: str = "A2") -> dict:
    assert api.post("/api/learner", json={"level": level}).status_code == 201
    resp = api.post("/api/drills", json={"item_id": item_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


def prepare(api: TestClient, card: dict) -> dict:
    resp = api.post(f"/api/exercises/{card['exercise_id']}/prepare")
    assert resp.status_code == 200, resp.text
    return resp.json()


def post_answer(api: TestClient, drill: dict, card: dict, payload: dict):
    return api.post(
        f"/api/sessions/{drill['session_id']}/answers",
        json={"exercise_id": card["exercise_id"], "answer": payload},
    )


def exercises_of(drill: dict) -> list[dict]:
    return [c for c in drill["cards"] if c["type"] == "production"]


def of_subtype(drill: dict, subtype: str) -> list[dict]:
    return [c for c in exercises_of(drill) if c["subtype"] == subtype]


def solution_of(settings: Settings, card: dict) -> dict:
    with create_session_factory(settings)() as db:
        return db.get(Exercise, card["exercise_id"]).solution


def first_answered_drill(api: TestClient, drill: dict) -> None:
    intro = drill["cards"][0]
    resp = api.post(
        f"/api/sessions/{drill['session_id']}/answers", json={"exercise_id": intro["exercise_id"]}
    )
    assert resp.status_code == 200, resp.text


# --- creating drills -----------------------------------------------------------------------------


def test_drill_on_a_new_point_starts_with_the_intro(
    api: TestClient, migrated_settings: Settings
) -> None:
    drill = new_drill(api)
    assert drill["session_id"].startswith("drill-")
    first, *rest = drill["cards"]
    assert first["type"] == "grammar_intro" and first["item_id"] == "gram:cases"
    assert len(rest) == 6 and all(c["type"] == "production" for c in rest)
    assert all(c["status"] == "pending" for c in rest)
    assert [c["subtype"] for c in rest] == subtypes(6)
    with create_session_factory(migrated_settings)() as db:
        positions = [db.get(Exercise, c["exercise_id"]).prompt["drill_position"] for c in rest]
    assert positions == [f"{n} of 6" for n in range(1, 7)]


def test_drill_on_an_introduced_point_has_no_intro(api: TestClient) -> None:
    first_answered_drill(api, new_drill(api))
    second = api.post("/api/drills", json={"item_id": "gram:cases"}).json()
    assert [c["type"] for c in second["cards"]] == ["production"] * 6
    assert second["session_id"].startswith("drill-")


def test_drill_on_a_presumed_known_point_has_no_intro(api: TestClient) -> None:
    drill = new_drill(api, "gram:articles")  # A1 point, presumed known for an A2 learner
    assert all(c["type"] == "production" for c in drill["cards"])


def test_drill_rejects_lemmas_and_unknown_ids(api: TestClient) -> None:
    api.post("/api/learner", json={"level": "A2"})
    assert api.post("/api/drills", json={"item_id": "lex:tisch"}).status_code == 404
    assert api.post("/api/drills", json={"item_id": "gram:nope"}).status_code == 404


def test_drill_size_setting(api: TestClient) -> None:
    api.post("/api/learner", json={"level": "A2"})
    assert api.get("/api/settings").json()["drill_size"] == 6
    assert api.put("/api/settings", json={"drill_size": 3}).json()["drill_size"] == 3
    drill = api.post("/api/drills", json={"item_id": "gram:cases"}).json()
    assert len(exercises_of(drill)) == 3
    assert api.put("/api/settings", json={"drill_size": 2}).status_code == 422
    assert api.put("/api/settings", json={"drill_size": 11}).status_code == 422


# --- closed exercises ----------------------------------------------------------------------------


def test_prepare_choice_and_cloze(api: TestClient) -> None:
    drill = new_drill(api)
    choice = prepare(api, of_subtype(drill, "choice")[0])
    assert choice["status"] == "ready"
    assert choice["prompt"]["text"].count("___") == 1
    options = choice["prompt"]["options"]
    assert len(options) == 4 and len(set(options)) == 4
    cloze = prepare(api, of_subtype(drill, "cloze")[0])
    assert cloze["status"] == "ready"
    assert cloze["prompt"]["text"].count("___") == 1
    assert not cloze["prompt"].get("options")


def test_choice_answers(api: TestClient, migrated_settings: Settings) -> None:
    drill = new_drill(api)
    right_card, wrong_card = of_subtype(drill, "choice")[:2]
    for card in (right_card, wrong_card):
        prepare(api, card)

    correct = solution_of(migrated_settings, right_card)["correct_index"]
    result = post_answer(api, drill, right_card, {"choice": correct}).json()
    assert result["kind"] == "production" and result["outcome"] == "correct"

    wrong_index = (solution_of(migrated_settings, wrong_card)["correct_index"] + 1) % 4
    result = post_answer(api, drill, wrong_card, {"choice": wrong_index}).json()
    assert result["outcome"] == "major_errors"
    assert [i["outcome"] for i in result["items"]] == ["error"]
    with create_session_factory(migrated_settings)() as db:
        evaluation = db.get(Evaluation, result["evaluation_id"])
        assert evaluation.grader_version.startswith("closed.v1")


def test_choice_index_out_of_range_is_refused(api: TestClient) -> None:
    drill = new_drill(api)
    card = of_subtype(drill, "choice")[0]
    prepare(api, card)
    assert post_answer(api, drill, card, {"choice": 4}).status_code == 422
    assert post_answer(api, drill, card, {"choice": -1}).status_code == 422


def test_cloze_answers(api: TestClient, migrated_settings: Settings) -> None:
    drill = new_drill(api)
    right_card, wrong_card = of_subtype(drill, "cloze")[:2]
    for card in (right_card, wrong_card):
        prepare(api, card)

    filler = solution_of(migrated_settings, right_card)["reference_solutions"][0]
    result = post_answer(api, drill, right_card, {"text": filler.upper()}).json()
    assert result["outcome"] == "correct"

    reference = solution_of(migrated_settings, wrong_card)["reference_solutions"][0]
    result = post_answer(api, drill, wrong_card, {"text": "xyzzy"}).json()
    assert result["outcome"] == "major_errors"
    assert reference in result["corrected_sentence"]
    assert "___" not in result["corrected_sentence"]


def test_closed_exercises_make_no_grading_call(
    api: TestClient, stub: StubLLM, migrated_settings: Settings, lt: LT
) -> None:
    def refuse(req):
        raise AssertionError("closed exercises must not call grade_sentence")

    stub.grade_sentence = refuse  # type: ignore[method-assign]
    drill = new_drill(api)
    choice, cloze = of_subtype(drill, "choice")[0], of_subtype(drill, "cloze")[0]
    prepare(api, choice)
    prepare(api, cloze)
    index = solution_of(migrated_settings, choice)["correct_index"]
    assert post_answer(api, drill, choice, {"choice": index}).status_code == 200
    assert post_answer(api, drill, cloze, {"text": "nope"}).status_code == 200

    with create_session_factory(migrated_settings)() as db:
        graded = db.scalar(
            select(func.count()).select_from(LLMCall).where(LLMCall.task == "grade_sentence")
        )
        assert graded == 0
    assert lt.requests == 0


# --- history and contests ------------------------------------------------------------------------


def test_drill_appears_in_history(api: TestClient, migrated_settings: Settings) -> None:
    drill = new_drill(api)
    first_answered_drill(api, drill)
    card = of_subtype(drill, "choice")[0]
    prepare(api, card)
    index = solution_of(migrated_settings, card)["correct_index"]
    assert post_answer(api, drill, card, {"choice": index}).status_code == 200

    rows = api.get("/api/progress/history").json()["items"]
    row = next(r for r in rows if r["id"] == drill["session_id"])
    assert row["title"] == "Esercizi: Casi"


def test_wrong_drill_answer_can_be_contested(api: TestClient, migrated_settings: Settings) -> None:
    drill = new_drill(api)
    card = of_subtype(drill, "cloze")[0]
    prepare(api, card)
    wrong = post_answer(api, drill, card, {"text": "xyzzy"}).json()
    assert wrong["outcome"] == "major_errors"

    resp = api.post(f"/api/evaluations/{wrong['evaluation_id']}/contest", json={"reason": "ok"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["contest"]["verdict"] == "accepted"
    with create_session_factory(migrated_settings)() as db:
        assert db.scalar(select(func.count()).select_from(LearningEvent)) > 0
