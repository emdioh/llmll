from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.domain.budget import LearnerItemView as V
from app.domain.budget import QueueItem as Q
from app.domain.budget import candidate_queue

from .test_api_flow import answer, setup_learner

NOW = datetime(2026, 10, 5, tzinfo=UTC)
KNOWN = V("introduced")


def test_queue_orders_by_source_level_frequency_and_id() -> None:
    items = [
        Q("lex:b", "lemma", "A2", 5.0),
        Q("lex:a", "lemma", "A2", 6.0),
        Q("lex:c", "lemma", "A1", 3.0),
        Q("lex:d", "lemma", "A2", 5.0),
        Q("lex:art", "lemma", "B1", 2.0),
        Q("lex:opt", "lemma", "B1", 1.0),
        Q("lex:skip", "lemma", "A1", 7.0),
    ]
    states = {
        "lex:a": V("candidate", "wordlist"),
        "lex:b": V("candidate", "wordlist"),
        "lex:c": V("candidate", "wordlist"),
        "lex:d": V("candidate", "wordlist"),
        "lex:art": V("candidate", "article"),
        "lex:opt": V("candidate", "optin"),
        "lex:skip": V("unseen"),  # an unseen lemma is not a candidate
    }
    assert candidate_queue(states, items, NOW) == [
        "lex:opt",  # opt-in beats everything
        "lex:art",  # then article
        "lex:c",  # wordlist: level A1 first
        "lex:a",  # A2: higher frequency first
        "lex:b",  # ties on frequency: id
        "lex:d",
    ]


def test_queue_skips_items_with_unmet_prerequisites() -> None:
    items = [
        Q("lex:x", "lemma", "A1", 5.0, requires=("lex:base",)),
        Q("lex:y", "lemma", "A1", 4.0, requires=("lex:known", "lex:presumed")),
        Q("lex:z", "lemma", "A1", 3.0, requires=("lex:missing",)),
        Q("lex:w", "lemma", "A1", 2.0, requires=("lex:cand",)),
    ]
    states = {
        "lex:x": V("candidate", "wordlist"),
        "lex:y": V("candidate", "wordlist"),
        "lex:z": V("candidate", "wordlist"),
        "lex:w": V("candidate", "wordlist"),
        "lex:base": V("unseen"),
        "lex:known": KNOWN,
        "lex:presumed": V("presumed_known"),
        "lex:cand": V("candidate", "wordlist"),
    }
    assert candidate_queue(states, items, NOW) == ["lex:y"]
    states["lex:base"] = KNOWN
    assert candidate_queue(states, items, NOW) == ["lex:x", "lex:y"]


def test_queue_grammar_follows_curriculum_order_and_level_cap() -> None:
    items = [
        Q("gram:two", "grammar", "A2", order=2),
        Q("gram:one", "grammar", "A1", order=1),
        Q("cx:one", "construction", "A2", order=3),
        Q("gram:b1", "grammar", "B1", order=4),
        Q("gram:opt", "grammar", "B1", order=5),
    ]
    states = {i.item_id: V("unseen") for i in items}
    states["gram:opt"] = V("candidate", "optin")
    assert candidate_queue(states, items, NOW, max_level="A2") == [
        "gram:opt",  # explicit opt-in, even above the level
        "gram:one",
        "gram:two",
        "cx:one",
    ]
    assert candidate_queue(states, items, NOW) == ["gram:opt"]  # no cap: only candidates


def test_queue_ignores_suspended_and_introduced() -> None:
    items = [Q("lex:a", "lemma", "A1", 5.0), Q("lex:b", "lemma", "A1", 4.0)]
    states = {"lex:a": V("suspended"), "lex:b": KNOWN}
    assert candidate_queue(states, items, NOW) == []


def test_queue_endpoint_optin_and_optout(curriculum_client: TestClient) -> None:
    client = curriculum_client
    setup_learner(client, "A1")
    queue = client.get("/api/queue").json()
    assert queue["budget"] == {"lemmas_left": 20, "grammar_left": 2, "backlog": 0}
    ids = [e["item_id"] for e in queue["next"]]
    assert len(ids) == 11 and "lex:fenster" not in ids  # 10 A1 lemmas + gram:articles
    assert {e["source"] for e in queue["next"]} == {"wordlist"}
    assert queue["next"][0]["label"] and ids[0] == "gram:articles"  # grammar sorts first

    resp = client.post("/api/items/lex:fenster/optin")
    assert resp.status_code == 200
    assert resp.json() == {
        "item_id": "lex:fenster",
        "status": "candidate",
        "candidate_source": "optin",
    }
    queue = client.get("/api/queue").json()["next"]
    assert queue[0]["item_id"] == "lex:fenster" and queue[0]["source"] == "optin"

    resp = client.post("/api/items/lex:fenster/optout")
    assert resp.json() == {
        "item_id": "lex:fenster",
        "status": "suspended",
        "candidate_source": None,
    }
    assert "lex:fenster" not in [e["item_id"] for e in client.get("/api/queue").json()["next"]]
    # A re-levelling leaves the opted-out item alone, and opt-in reverses the opt-out.
    client.put("/api/learner", json={"level": "A2"})
    assert client.get("/api/items/lex:fenster").json()["status"] == "suspended"
    assert client.post("/api/items/lex:fenster/optin").json()["status"] == "candidate"

    # Wordlist candidates can be opted out too; introduced items cannot.
    assert client.post("/api/items/lex:apfel/optout").json()["status"] == "suspended"
    assert client.post("/api/items/lex:tisch/optout").status_code == 409  # presumed known (A2)
    assert client.post("/api/items/lex:tisch/optin").json()["status"] == "presumed_known"
    assert client.post("/api/items/nope/optin").status_code == 404
    assert client.post("/api/items/nope/optout").status_code == 404


def test_queue_requires_learner(curriculum_client: TestClient) -> None:
    assert curriculum_client.get("/api/queue").status_code == 404


def test_budget_shrinks_with_introductions_and_backlog(
    curriculum_client: TestClient, clock
) -> None:
    client = curriculum_client
    setup_learner(client, "A1")
    client.put("/api/settings", json={"weekly_new_lemmas": 12, "review_cap": 3})
    session = client.post("/api/sessions").json()
    for card in session["cards"]:  # 10 intro cards
        answer(client, session["session_id"], card)
    assert client.get("/api/queue").json()["budget"] == {
        "lemmas_left": 2,
        "grammar_left": 2,
        "backlog": 0,
    }
    # A week later the 10 lemmas are due: backlog 20 memories (two facets each) > 2 * review_cap.
    clock.advance(days=8)
    budget = client.get("/api/queue").json()["budget"]
    assert budget["backlog"] == 20 and budget["lemmas_left"] == 0 and budget["grammar_left"] == 0
