from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import main as cli_main
from app.config import Settings, get_settings
from app.services.learner import projection_config, settings_of
from app.services.sessions import _review_card
from app.store.db import create_session_factory
from app.store.models import (
    Exercise,
    ItemMemory,
    Learner,
    LearnerItem,
    LearningEvent,
)

from .conftest import Clock

A1_LEMMAS = 10  # lemmas at level A1 in the fixture curriculum


def snapshot(settings: Settings) -> dict:
    with create_session_factory(settings)() as session:
        rows = session.scalars(select(ItemMemory)).all()
        return {
            (r.item_id, r.facet): (
                r.fsrs_card,
                r.due,
                r.stability,
                r.difficulty,
                r.mastery,
                r.n_effective,
                r.tag_error_counts,
                r.last_event_id,
                r.projection_version,
            )
            for r in rows
        }


def setup_learner(client: TestClient, level: str = "A1", new_per_session: int | None = 20) -> None:
    resp = client.post("/api/learner", json={"level": level})
    assert resp.status_code == 201, resp.text
    # M1 tests exercise the flashcard-only mode (no production slots); lift the per-session cap
    # so tests see the whole budget.
    changes: dict = {"production_slots": 0}
    if new_per_session is not None:
        changes["new_per_session"] = new_per_session
    client.put("/api/settings", json=changes)


def label_of(client: TestClient, item_id: str) -> str:
    return client.get(f"/api/items/{item_id}").json()["label"]


def answer(client: TestClient, session_id: str, card: dict, **body) -> dict:
    resp = client.post(
        f"/api/sessions/{session_id}/answers", json={"exercise_id": card["exercise_id"], **body}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_learner_setup(curriculum_client: TestClient) -> None:
    client = curriculum_client
    assert client.get("/api/learner").status_code == 404
    assert client.post("/api/sessions").status_code == 404
    resp = client.post("/api/learner", json={"level": "A2", "known_languages": ["it", "en"]})
    assert resp.status_code == 201
    body = resp.json()
    assert body["level"] == "A2" and body["known_languages"] == ["it", "en"]
    assert body["settings"] == {
        "weekly_new_lemmas": 20,
        "weekly_new_grammar": 2,
        "desired_retention": 0.85,
        "review_cap": 15,
        "new_per_session": 5,
        "production_slots": 2,
    }
    assert client.post("/api/learner", json={"level": "A1"}).status_code == 409
    assert client.get("/api/learner").json()["explanation_language"] == "it"

    # Items below A2 are presumed known; lemmas at A2 are candidates; the rest is unseen.
    status_of = {i["id"]: i["status"] for i in client.get("/api/items?limit=100").json()["items"]}
    assert status_of["lex:tisch"] == "presumed_known"
    assert status_of["gram:articles"] == "presumed_known"
    assert status_of["lex:fenster"] == "candidate"
    assert status_of["gram:cases"] == "unseen"
    assert status_of["cx:lust-haben-auf"] == "unseen"


def test_put_learner_reapplies_level_only_without_events(curriculum_client: TestClient) -> None:
    client = curriculum_client
    setup_learner(client, "A1")
    session = client.post("/api/sessions").json()
    intro = next(c for c in session["cards"] if c["item_id"] == "lex:tisch")
    answer(client, session["session_id"], intro)

    assert client.put("/api/learner", json={"level": "A2"}).json()["level"] == "A2"
    status_of = {i["id"]: i["status"] for i in client.get("/api/items?limit=100").json()["items"]}
    assert status_of["lex:tisch"] == "introduced"  # has events: untouched
    assert status_of["lex:katze"] == "presumed_known"  # no events: re-evaluated
    assert status_of["lex:fenster"] == "candidate"


def test_settings(curriculum_client: TestClient) -> None:
    client = curriculum_client
    assert client.get("/api/settings").status_code == 404
    setup_learner(client)
    resp = client.put("/api/settings", json={"review_cap": 5, "desired_retention": 0.9})
    assert resp.status_code == 200
    assert resp.json()["review_cap"] == 5 and resp.json()["weekly_new_lemmas"] == 20
    assert client.get("/api/settings").json()["desired_retention"] == 0.9
    assert client.put("/api/settings", json={"desired_retention": 0.2}).status_code == 422


def test_full_flow_and_replay(
    curriculum_client: TestClient, migrated_settings: Settings, clock: Clock, monkeypatch
) -> None:
    client = curriculum_client
    setup_learner(client, "A1")

    # Day 0: only intro cards (nothing is due yet), budget-limited to the weekly 20.
    session = client.post("/api/sessions").json()
    cards = session["cards"]
    assert len(cards) == A1_LEMMAS
    assert {c["type"] for c in cards} == {"flashcard_intro"}
    tisch = next(c for c in cards if c["item_id"] == "lex:tisch")
    assert tisch["prompt"]["de"] == "der Tisch" and tisch["prompt"]["plural"] == "Tische"
    assert "tavolo" in tisch["prompt"]["translation_it"]
    katze = next(c for c in cards if c["item_id"] == "lex:katze")
    assert "maschile" in katze["prompt"]["interference_note"]
    for card in cards:
        result = answer(client, session["session_id"], card)
        assert result["memory"]["due"] is not None
    # An answered exercise cannot be answered again; unknown exercises are 404.
    assert (
        client.post(
            f"/api/sessions/{session['session_id']}/answers",
            json={"exercise_id": cards[0]["exercise_id"]},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/sessions/{session['session_id']}/answers", json={"exercise_id": "nope"}
        ).status_code
        == 404
    )
    items = client.get("/api/items?status=introduced").json()
    assert items["total"] == A1_LEMMAS
    assert {m["facet"] for m in items["items"][0]["memory"]} == {"recognition", "production"}
    assert client.get("/api/items?status=candidate").json()["total"] == 0  # A2 is unseen

    # Right after: nothing due, and no more candidates.
    assert client.post("/api/sessions").json()["cards"] == []

    # Day 10: everything is due. One card per item (recognition first), capped at review_cap.
    clock.advance(days=10)
    client.put("/api/settings", json={"review_cap": 6})
    session = client.post("/api/sessions").json()
    cards = session["cards"]
    assert len(cards) == 6
    assert len({c["item_id"] for c in cards}) == 6
    assert {c["type"] for c in cards} == {"flashcard_recognition"}
    assert "correct_index" not in str(cards)

    # Recognition card: 4 distinct translations, exactly one correct.
    rec = cards[0]
    assert len(set(rec["prompt"]["options"])) == 4
    item = client.get(f"/api/items/{rec['item_id']}").json()
    correct_idx = rec["prompt"]["options"].index(item["payload"]["translations"]["it"])
    ok = answer(client, session["session_id"], rec, answer={"choice": correct_idx})
    assert ok["outcome"] == "correct" and ok["memory"]["facet"] == "recognition"
    assert ok["expected"]["correct_index"] == correct_idx
    assert ok["expected"]["translation_it"] == item["payload"]["translations"]["it"]
    bad = answer(client, session["session_id"], cards[1], answer={"choice": 99})
    assert bad["outcome"] == "error"

    # Second session the same day: the two answered items now come up as production cards.
    client.put("/api/settings", json={"review_cap": 20})
    session = client.post("/api/sessions").json()
    prod = [c for c in session["cards"] if c["type"] == "flashcard_production"]
    assert {c["item_id"] for c in prod} == {cards[0]["item_id"], cards[1]["item_id"]}
    assert len(session["cards"]) == A1_LEMMAS
    first, second = prod
    for card in prod:
        assert card["hint"] and card["prompt"]["it"]

    label = label_of(client, first["item_id"])
    wrong = "xyz"
    if first["prompt"]["needs_article"]:
        article, _, noun = label.partition(" ")
        wrong = f"{'das' if article != 'das' else 'der'} {noun}"
    res = answer(client, session["session_id"], first, answer={"text": wrong})
    assert res["outcome"] == "error"
    if first["prompt"]["needs_article"]:
        assert res["diagnostic_tags"] == ["gender"] and "Attenzione" in res["feedback_it"]
    res = answer(
        client,
        session["session_id"],
        second,
        answer={"text": label_of(client, second["item_id"])},
        used_hint=True,
    )
    assert res["outcome"] == "assisted"  # a hint turns correct into assisted

    # Replay must reproduce exactly the incrementally built projection.
    before = snapshot(migrated_settings)
    assert before
    monkeypatch.setenv("LLMLL_DATABASE_URL", migrated_settings.database_url)
    get_settings.cache_clear()
    try:
        assert cli_main(["replay"]) == 0
    finally:
        get_settings.cache_clear()
    assert snapshot(migrated_settings) == before


def test_presumed_known_first_review(
    curriculum_client: TestClient, migrated_settings: Settings, clock: Clock
) -> None:
    client = curriculum_client
    setup_learner(client, "A2")  # A1 lemmas are presumed known
    factory = create_session_factory(migrated_settings)
    with factory() as db:
        learner = db.get(Learner, 1)
        from app.store.models import Item

        item = db.get(Item, "lex:tisch")
        cfg = projection_config(settings_of(learner))
        exercise, card = _review_card(db, 1, "s-test", item, "production", cfg, clock.now)
        db.add(exercise)
        db.commit()
    res = answer(client, "s-test", {"exercise_id": card.exercise_id}, answer={"text": "der Tisch"})
    assert res["outcome"] == "correct"
    with factory() as db:
        events = db.scalars(
            select(LearningEvent)
            .where(LearningEvent.item_id == "lex:tisch")
            .order_by(LearningEvent.id)
        ).all()
        assert [e.kind for e in events] == ["review", "status_change"]
        assert events[0].presumed_known is True
        assert db.get(LearnerItem, (1, "lex:tisch")).status == "introduced"
        memory = db.get(ItemMemory, (1, "lex:tisch", "production"))
        # Easy rating: farther out than a plain Good would be.
        assert memory.stability > 3.5
    item_detail = client.get("/api/items/lex:tisch").json()
    assert item_detail["status"] == "introduced"

    # Datetimes come back timezone-aware.
    with factory() as db:
        assert db.get(ItemMemory, (1, "lex:tisch", "production")).due.tzinfo is not None
        assert db.get(Learner, 1).created_at.tzinfo is not None
    assert datetime.fromisoformat(item_detail["memory"][0]["due"]) > clock.now.astimezone(UTC)


def test_settings_retention_change_triggers_replay(
    curriculum_client: TestClient, migrated_settings: Settings, clock: Clock
) -> None:
    client = curriculum_client
    setup_learner(client)
    session = client.post("/api/sessions").json()
    for card in session["cards"]:
        answer(client, session["session_id"], card)
    before = snapshot(migrated_settings)
    client.put("/api/settings", json={"desired_retention": 0.95})
    after = snapshot(migrated_settings)
    assert before.keys() == after.keys()
    key = ("lex:tisch", "production")
    assert after[key][8] != before[key][8]  # projection_version changed
    assert after[key][1] <= before[key][1]  # higher retention: due sooner or equal


def test_corpus_and_grammar(curriculum_client: TestClient) -> None:
    client = curriculum_client
    listing = client.get("/api/items?kind=lemma&q=tavolo").json()
    assert listing["total"] == 1 and listing["items"][0]["label"] == "der Tisch"
    assert listing["items"][0]["status"] is None  # no learner yet
    assert client.get("/api/items?level=A2&limit=2&offset=1").json()["total"] == 6
    detail = client.get("/api/items/lex:tisch").json()
    assert detail["payload"]["plural"] == "Tische" and detail["interference"] == {"it_gender": "m"}
    assert client.get("/api/items/lex:missing").status_code == 404
    grammar = client.get("/api/grammar").json()
    assert [g["id"] for g in grammar] == ["gram:articles", "gram:cases"]
    full = client.get("/api/grammar/gram:cases").json()
    assert full["requires"] == ["gram:articles"] and len(full["examples"]) == 2
    assert client.get("/api/grammar/lex:tisch").status_code == 404


def test_new_lemmas_respect_budget_and_priority(
    curriculum_client: TestClient, migrated_settings: Settings
) -> None:
    client = curriculum_client
    setup_learner(client)
    client.put("/api/settings", json={"weekly_new_lemmas": 3})
    with create_session_factory(migrated_settings)() as db:
        row = db.get(LearnerItem, (1, "lex:eltern"))
        row.candidate_source = "optin"
        db.commit()
    cards = client.post("/api/sessions").json()["cards"]
    assert len(cards) == 3
    assert cards[0]["item_id"] == "lex:eltern"  # opt-in first, then by frequency
    session = client.post("/api/sessions").json()
    for card in session["cards"]:
        answer(client, session["session_id"], card)
    assert client.post("/api/sessions").json()["cards"] == []  # weekly budget used up


def test_exercises_are_persisted(
    curriculum_client: TestClient, migrated_settings: Settings
) -> None:
    client = curriculum_client
    setup_learner(client)
    session = client.post("/api/sessions").json()
    with create_session_factory(migrated_settings)() as db:
        rows = db.scalars(select(Exercise)).all()
        assert len(rows) == len(session["cards"])
        assert all(
            r.generator == "flashcards.v1" and r.session_id == session["session_id"] for r in rows
        )


def test_openapi_has_operation_ids(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    ids = {op["operationId"] for path in spec["paths"].values() for op in path.values()}
    assert {"getLearner", "createSession", "submitAnswer", "listItems", "getGrammar"} <= ids
    pytest.importorskip("fastapi")


def test_new_per_session_cap(curriculum_client: TestClient) -> None:
    client = curriculum_client
    setup_learner(client, new_per_session=None)
    assert client.get("/api/settings").json()["new_per_session"] == 5
    assert len(client.post("/api/sessions").json()["cards"]) == 5  # min(budget 20, cap 5)
    client.put("/api/settings", json={"new_per_session": 2, "weekly_new_lemmas": 1})
    assert len(client.post("/api/sessions").json()["cards"]) == 1  # budget is the lower bound
    client.put("/api/settings", json={"new_per_session": 0})
    assert client.post("/api/sessions").json()["cards"] == []
    assert client.put("/api/settings", json={"new_per_session": 21}).status_code == 422
    assert client.put("/api/settings", json={"new_per_session": -1}).status_code == 422
