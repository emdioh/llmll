# ruff: noqa: F811
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from wordfreq import zipf_frequency

from app.config import Settings
from app.curriculum.loader import load_curriculum
from app.domain.config import PlacementConfig
from app.domain.placement import (
    PlacementGrammar,
    PlacementLemma,
    PlacementResult,
    estimate_level,
    sample_placement_lemmas,
    select_placement_grammar,
)
from app.store.db import create_session_factory
from app.store.models import Exercise, LearnerItem, LearningEvent, Placement

from .test_api_production import LT, StubLLM, answer, api, lt, stub  # noqa: F401

REAL = Path(__file__).resolve().parents[2] / "curriculum" / "de"


def synthetic_lemmas() -> list[PlacementLemma]:
    lemmas = []
    for level in ("A1", "A2", "B1"):
        for n in range(30):
            lemmas.append(PlacementLemma(f"lex:{level.lower()}-{n:02d}", level, 7.0 - n / 10))
    return lemmas


def test_sampling_bands_terciles_and_determinism() -> None:
    lemmas = synthetic_lemmas()
    by_id = {lemma.item_id: lemma for lemma in lemmas}
    ids = sample_placement_lemmas(lemmas, "A2", seed="s1")
    assert len(ids) == len(set(ids)) == 30
    assert Counter(by_id[i].level for i in ids) == {"A1": 10, "A2": 10, "B1": 10}
    for level in ("A1", "A2", "B1"):
        band = sorted((p for p in lemmas if p.level == level), key=lambda p: -p.frequency_zipf)
        terciles = [{p.item_id for p in band[i * 10 : (i + 1) * 10]} for i in range(3)]
        picked = [i for i in ids if by_id[i].level == level]
        counts = sorted(len(t & set(picked)) for t in terciles)
        assert counts == [3, 3, 4]  # stratified: 4 / 3 / 3
    assert ids == sample_placement_lemmas(lemmas, "A2", seed="s1")  # deterministic
    assert set(ids) != set(sample_placement_lemmas(lemmas, "A2", seed="s2"))
    # Easiest band first.
    levels = [by_id[i].level for i in ids]
    assert levels == sorted(levels)


def test_sampling_redistributes_missing_bands() -> None:
    lemmas = [p for p in synthetic_lemmas() if p.level != "B1"]
    ids = sample_placement_lemmas(lemmas, "A2", seed="x")
    levels = Counter(next(p.level for p in lemmas if p.item_id == i) for i in ids)
    assert sum(levels.values()) == 30 and levels["A2"] >= 15  # the declared band gets the extra
    # Declared B1 (no B2 lemmas): bands A2 and B1 share the 30 slots.
    ids = sample_placement_lemmas(synthetic_lemmas(), "B1", seed="x")
    assert len(ids) == 30
    # Fewer lemmas than requested: everything there is.
    few = [PlacementLemma("lex:a", "A1", 5.0), PlacementLemma("lex:b", "A2", 4.0)]
    assert sorted(sample_placement_lemmas(few, "A1", seed=1)) == ["lex:a", "lex:b"]
    assert sample_placement_lemmas([], "A1", seed=1) == []


def test_sampling_on_the_real_curriculum() -> None:
    if not REAL.is_dir():
        pytest.skip("curriculum/de does not exist")
    items = [i for i in load_curriculum(REAL).items if i.kind == "lemma"]
    lemmas = [
        PlacementLemma(i.id, i.level, float(zipf_frequency(i.payload["lemma"], "de")))
        for i in items
    ]
    for declared, expected in (("A2", {"A1", "A2", "B1"}), ("B1", {"A2", "B1"})):
        ids = sample_placement_lemmas(lemmas, declared, seed="real")
        levels = Counter(next(p.level for p in lemmas if p.item_id == i) for i in ids)
        assert set(levels) == expected and sum(levels.values()) == 30
        if declared == "A2":
            assert set(levels.values()) == {10}


def grammar_items() -> list[PlacementGrammar]:
    spec = [
        ("gram:svo-word-order", "A1"),
        ("gram:adverb-fronting", "A1"),
        ("gram:verbal-bracket", "A1"),
        ("gram:dative-case", "A1"),
        ("gram:inversion-v2", "A2"),
        ("gram:perfekt", "A2"),
        ("gram:modal-verbs", "A2"),
        ("gram:verb-final-subordinate", "A2"),
        ("gram:relative-clauses", "B1"),
        ("gram:passive-werden", "B1"),
    ]
    return [PlacementGrammar(i, lvl, n) for n, (i, lvl) in enumerate(spec)]


def test_grammar_selection_includes_word_order_stages() -> None:
    items = grammar_items()
    a2 = select_placement_grammar(items, "A2")
    assert len(a2) == 4 and "gram:inversion-v2" in a2 and "gram:verbal-bracket" in a2
    assert "gram:verb-final-subordinate" not in a2  # a B1 stage
    b1 = select_placement_grammar(items, "B1")
    assert len(b1) == 4 and "gram:verb-final-subordinate" in b1 and "gram:inversion-v2" in b1
    a1 = select_placement_grammar(items, "A1")
    assert len(a1) == 4 and "gram:verbal-bracket" in a1 and "gram:adverb-fronting" in a1
    assert a2 == select_placement_grammar(items, "A2")
    # Other points: declared level and the one above.
    assert {"gram:perfekt"} <= set(a2) or {"gram:relative-clauses"} <= set(a2)
    only = select_placement_grammar(items[:2], "A2")
    assert sorted(only) == ["gram:adverb-fronting", "gram:svo-word-order"]
    assert select_placement_grammar([], "A2") == []


def results(level: str, outcomes: str) -> list[PlacementResult]:
    names = {"c": "correct", "a": "assisted", "e": "error"}
    return [PlacementResult(level, names[o]) for o in outcomes]


def test_estimate_level_thresholds() -> None:
    cfg = PlacementConfig()
    assert cfg.raise_threshold == 0.7 and cfg.lower_threshold == 0.4
    # Raise: >= 70% one band above (7 of 10), not 6 of 10.
    assert estimate_level(results("B1", "cccccccccc"), "A2") == "B1"
    assert estimate_level(results("B1", "ccccccceee"), "A2") == "B1"
    assert estimate_level(results("B1", "cccccceeee"), "A2") == "A2"
    # Assisted answers count half.
    assert estimate_level(results("B1", "ccccaaaaee"), "A2") == "A2"  # 6/10
    assert estimate_level(results("B1", "cccccaaaae"), "A2") == "B1"  # 7/10
    # Lower: < 40% at the declared band (3 of 10 yes, 4 of 10 no).
    assert estimate_level(results("A2", "cccceeeeee"), "A2") == "A2"  # exactly 40%
    assert estimate_level(results("A2", "cceeeeeeee"), "A2") == "A1"
    assert estimate_level(results("A2", "cceeeeeeee"), "A1") == "A1"  # no band below A1
    # Too little evidence changes nothing.
    assert estimate_level(results("B1", "ccc"), "A2") == "A2"
    assert estimate_level(results("A2", "eee"), "A2") == "A2"
    assert estimate_level([], "A2") == "A2"
    # Both vocabulary and grammar results of a band count together; other bands are ignored.
    mixed = results("A2", "ccc") + results("A2", "e") + results("A1", "eeeeeeee")
    assert estimate_level(mixed, "A2") == "A2"
    # Raising wins over lowering when the band above is strong.
    assert estimate_level(results("B1", "cccc") + results("A2", "eeee"), "A2") == "B1"


# --- API ---------------------------------------------------------------------------------------


def db_of(settings: Settings):
    return create_session_factory(settings)()


def correct_choice(settings: Settings, card: dict) -> int:
    with db_of(settings) as db:
        return db.get(Exercise, card["exercise_id"]).solution["correct_index"]


def answer_vocab(api: TestClient, settings: Settings, pid: str, card: dict, right: bool) -> None:
    correct = correct_choice(settings, card)
    choice = correct if right else (correct + 1) % 4
    resp = api.post(
        f"/api/sessions/{pid}/answers",
        json={"exercise_id": card["exercise_id"], "answer": {"choice": choice}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == ("correct" if right else "error")


def status_of(settings: Settings, item_id: str) -> str:
    with db_of(settings) as db:
        return db.get(LearnerItem, (1, item_id)).status


def test_placement_raises_level_and_reapplies_statuses(
    api: TestClient, migrated_settings: Settings
) -> None:
    assert api.post("/api/placement").status_code == 404  # no learner yet
    assert api.post("/api/learner", json={"level": "A1"}).status_code == 201
    resp = api.post("/api/placement")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    pid = body["placement_id"]
    vocab = [c for c in body["cards"] if c["type"] == "flashcard_recognition"]
    grammar = [c for c in body["cards"] if c["type"] == "production"]
    assert len(vocab) == 14 and len(grammar) == 2  # all fixture lemmas, two grammar points
    assert body["cards"][: len(vocab)] == vocab  # vocabulary first
    assert {c["subtype"] for c in grammar} == {"guided"}
    assert {c["status"] for c in grammar} == {"pending"}
    assert {c["item_ids"][0] for c in grammar} == {"gram:articles", "gram:cases"}

    skipped = vocab[0]["item_id"]
    assert status_of(migrated_settings, skipped) == "candidate"
    for card in vocab[1:]:
        answer_vocab(api, migrated_settings, pid, card, right=True)
    # Grammar goes through prepare (M2 generation) and the normal answers endpoint.
    articles = next(c for c in grammar if c["item_ids"][0] == "gram:articles")
    ready = api.post(f"/api/exercises/{articles['exercise_id']}/prepare").json()
    assert ready["status"] == "ready"
    with db_of(migrated_settings) as db:
        reference = db.get(Exercise, articles["exercise_id"]).solution["reference_solutions"][0]
    graded = answer(api, {"session_id": pid}, ready, reference)
    assert graded["outcome"] == "correct"

    with db_of(migrated_settings) as db:
        events = list(db.scalars(select(LearningEvent)))
        assert events and {e.context for e in events} == {"placement"}
        assert {e.kind for e in events} >= {"review", "introduce"}
        assert {e.facet for e in events if e.kind == "review"} == {"recognition", "production"}

    finished = api.post(f"/api/placement/{pid}/finish")
    assert finished.status_code == 200, finished.text
    result = finished.json()
    assert result["estimated_level"] == "A2" and result["changed"] is True
    assert result["previous_level"] == "A1" and result["answered"] == 14
    assert result["bands"]["A2"] == {"n": 4, "score": 1.0}
    assert api.get("/api/learner").json()["level"] == "A2"
    # The assessment does not use up the weekly budget of new items.
    budget = api.get("/api/queue").json()["budget"]
    assert budget["lemmas_left"] == 20 and budget["grammar_left"] == 2
    # Statuses were re-applied only to items without events.
    assert status_of(migrated_settings, skipped) == "presumed_known"  # A1 item, never answered
    assert status_of(migrated_settings, "lex:fenster") == "introduced"  # answered: untouched
    assert status_of(migrated_settings, "lex:gehen") in ("introduced", "presumed_known")
    with db_of(migrated_settings) as db:
        row = db.get(Placement, pid)
        assert row.finished_at is not None and row.estimated_level == "A2" and row.changed
    # Finishing twice or an unknown placement is refused; the session id is the placement id.
    assert api.post(f"/api/placement/{pid}/finish").status_code == 409
    assert api.post("/api/placement/placement-nope/finish").status_code == 404
    other = api.post(
        "/api/sessions/placement-other/answers", json={"exercise_id": vocab[1]["exercise_id"]}
    )
    assert other.status_code == 404
    # Events of normal sessions are not tagged.
    session = api.post("/api/sessions").json()
    card = next(c for c in session["cards"] if c["type"] == "grammar_intro")
    assert (
        api.post(
            f"/api/sessions/{session['session_id']}/answers",
            json={"exercise_id": card["exercise_id"]},
        ).status_code
        == 200
    )
    with db_of(migrated_settings) as db:
        assert {e.context for e in db.scalars(select(LearningEvent))} == {"placement", None}


def test_placement_lowers_level(api: TestClient, migrated_settings: Settings) -> None:
    api.post("/api/learner", json={"level": "A2"})
    body = api.post("/api/placement").json()
    pid = body["placement_id"]
    vocab = [c for c in body["cards"] if c["type"] == "flashcard_recognition"]
    levels = {c["item_id"]: api.get(f"/api/items/{c['item_id']}").json()["level"] for c in vocab}
    skipped = next(c for c in vocab if levels[c["item_id"]] == "A1")
    for card in vocab:
        if card is skipped:
            continue
        answer_vocab(api, migrated_settings, pid, card, right=levels[card["item_id"]] == "A1")
    assert status_of(migrated_settings, skipped["item_id"]) == "presumed_known"
    result = api.post(f"/api/placement/{pid}/finish").json()
    assert result["estimated_level"] == "A1" and result["changed"] is True
    assert api.get("/api/learner").json()["level"] == "A1"
    assert status_of(migrated_settings, skipped["item_id"]) == "candidate"  # now at the level
    assert status_of(migrated_settings, "lex:fenster") == "introduced"  # failed, but has events


def test_placement_without_clear_evidence_keeps_the_level(
    api: TestClient, migrated_settings: Settings
) -> None:
    api.post("/api/learner", json={"level": "A2"})
    body = api.post("/api/placement").json()
    pid = body["placement_id"]
    for i, card in enumerate(c for c in body["cards"] if c["type"] == "flashcard_recognition"):
        answer_vocab(api, migrated_settings, pid, card, right=i % 2 == 0)
    result = api.post(f"/api/placement/{pid}/finish").json()
    assert result["estimated_level"] == "A2" and result["changed"] is False
    assert api.get("/api/learner").json()["level"] == "A2"
    # Re-runnable: a second placement works.
    assert api.post("/api/placement").status_code == 201
    with db_of(migrated_settings) as db:
        assert len(db.scalars(select(Placement)).all()) == 2
