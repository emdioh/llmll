# ruff: noqa: F811
from fastapi.testclient import TestClient

from .test_api_production import api, lt, stub  # noqa: F401
from .test_drills import first_answered_drill, new_drill

LEARNER_FIELDS = ("status", "state", "mastery", "introduced_at", "last_practiced", "due")


def by_id(client: TestClient) -> dict[str, dict]:
    resp = client.get("/api/grammar")
    assert resp.status_code == 200, resp.text
    return {g["id"]: g for g in resp.json()}


def test_grammar_list_without_learner_has_null_learner_fields(
    curriculum_client: TestClient,
) -> None:
    rows = by_id(curriculum_client)
    assert "gram:cases" in rows
    for row in rows.values():
        assert all(row[f] is None for f in LEARNER_FIELDS)


def test_grammar_list_fresh_learner(curriculum_client: TestClient) -> None:
    assert curriculum_client.post("/api/learner", json={"level": "A2"}).status_code == 201
    rows = by_id(curriculum_client)
    cases = rows["gram:cases"]
    assert cases["state"] == "new"
    assert cases["status"] in (None, "unseen", "candidate")
    assert cases["mastery"] is None
    assert cases["introduced_at"] is None and cases["last_practiced"] is None
    articles = rows["gram:articles"]  # A1 point, presumed known for an A2 learner
    assert articles["status"] == "presumed_known"
    assert articles["state"] == "presumed_known"
    assert articles["mastery"] is None


def test_grammar_list_after_intro(api: TestClient) -> None:
    drill = new_drill(api)
    assert by_id(api)["gram:cases"]["introduced_at"] is None
    first_answered_drill(api, drill)
    cases = by_id(api)["gram:cases"]
    assert cases["status"] == "introduced"
    assert cases["introduced_at"] is not None
    assert cases["last_practiced"] is not None
    assert cases["mastery"] is not None and 0 <= cases["mastery"] <= 1
    assert cases["state"] != "new"
    assert cases["due"] is not None
