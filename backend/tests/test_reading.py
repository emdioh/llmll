import time
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import main as cli_main
from app.config import Settings, get_settings
from app.curriculum.importer import import_curriculum
from app.curriculum.loader import load_curriculum
from app.llm.calls import SqlCallRecorder
from app.llm.client import LLMError, LLMUnavailable
from app.llm.fake import FakeLLMClient
from app.llm.types import SimplifiedText
from app.main import create_app
from app.nlp.analyzer import FakeAnalyzer
from app.nlp.extract import fetch_article
from app.store.db import create_session_factory
from app.store.models import (
    GlossCache,
    Item,
    LearnerItem,
    LearningEvent,
    LLMCall,
    ReadingSession,
    SourceText,
    TextVersion,
)

from .conftest import FIXTURES, Clock, import_fixture
from .test_api_flow import snapshot

# "Der" and "und" are not in the fixture lexicon, so the fake analyzer (identity lemmas) counts them
# as unknown words: the first attempt of the fake LLM has a coverage below the target.
SOURCE = (
    "Tisch Katze Haus Buch Stuhl Brot Mädchen Eltern gehen schön Der Tisch und die Katze. "
    "Das Haus ist schön und das Buch ist da. Fenster Zeitung."
)
ENGLISH = "The table and the cat are in the house and the book is on the chair today."


class StubLLM(FakeLLMClient):
    """Fake LLM with call counters and failure hooks."""

    def __init__(self, recorder=None) -> None:
        super().__init__(recorder)
        self.simplify_requests: list = []
        self.gloss_calls = 0
        self.fail_simplify_after: int | None = None
        self.simplify_error: Exception = LLMUnavailable("down")
        self.fixed_text: str | None = None

    def simplify_text(self, req):
        self.simplify_requests.append(req)
        if self.fail_simplify_after is not None and len(self.simplify_requests) > (
            self.fail_simplify_after
        ):
            raise self.simplify_error
        if self.fixed_text is not None:
            result = SimplifiedText(title="Fest", paragraphs=[self.fixed_text])
            self._log("simplify_text", req, result, time.monotonic())
            return result
        return super().simplify_text(req)

    def gloss(self, req):
        self.gloss_calls += 1
        return super().gloss(req)


@pytest.fixture
def llm(migrated_settings: Settings, clock: Clock) -> StubLLM:
    return StubLLM(SqlCallRecorder(create_session_factory(migrated_settings), clock))


@pytest.fixture
def client(migrated_settings: Settings, clock: Clock, llm: StubLLM) -> Iterator[TestClient]:
    import_fixture(migrated_settings, when=clock.now)
    html = (FIXTURES / "article.html").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if "paywall" in str(request.url):
            return httpx.Response(402, text="pay")
        return httpx.Response(200, content=html)

    def fetch(url: str):
        return fetch_article(url, httpx.MockTransport(handler))

    app = create_app(
        migrated_settings, now=clock, llm=llm, analyzer=FakeAnalyzer(), article_fetcher=fetch
    )
    with TestClient(app) as test_client:
        test_client.post("/api/learner", json={"level": "A2"})
        test_client.put("/api/settings", json={"production_slots": 0})
        yield test_client


def create_text(client: TestClient, text: str = SOURCE, **extra) -> dict:
    resp = client.post("/api/texts", json={"text": text, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def start(client: TestClient, text: dict) -> int:
    resp = client.post(f"/api/texts/{text['id']}/reading")
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def token_index(text: dict, surface: str) -> int:
    body = text["version"]["body"]
    for t in text["version"]["tokens"]:
        if body[t["start"] : t["end"]] == surface:
            return t["i"]
    raise AssertionError(surface)


def status_of(settings: Settings, item_id: str) -> tuple[str, str | None]:
    with create_session_factory(settings)() as db:
        row = db.get(LearnerItem, (1, item_id))
        return row.status, row.candidate_source


def events(settings: Settings, **where) -> list[LearningEvent]:
    with create_session_factory(settings)() as db:
        stmt = select(LearningEvent).order_by(LearningEvent.id)
        for key, value in where.items():
            stmt = stmt.where(getattr(LearningEvent, key) == value)
        return list(db.scalars(stmt))


# --- simplification loop ---------------------------------------------------------------------


def test_create_text_runs_the_coverage_loop(client: TestClient, llm: StubLLM) -> None:
    text = create_text(client)
    version = text["version"]
    # attempt 1 had unknown words ("Der", "und", "die", "Zeitung"...), attempt 2 replaced them
    assert [r.replace_words != [] for r in llm.simplify_requests] == [False, True]
    assert version["attempt"] == 2 and version["coverage"] >= 0.95
    assert version["level"] == "A2"
    assert text["original"] == SOURCE.strip() and text["source_language"] == "de"
    first = llm.simplify_requests[0]
    assert first.mode == "simplify" and first.level == "A2" and first.explanation_language == "it"
    assert "Tisch" in first.allowed_lemmas and "Fenster" not in first.allowed_lemmas
    assert first.candidate_lemmas == ["Fenster"]  # A2 words of the source still candidates
    assert llm.simplify_requests[1].previous_text
    assert {w.lemma for w in llm.simplify_requests[1].replace_words} >= {"Der", "und"}
    # tokens carry the classification
    body = version["body"]
    classes = {
        body[t["start"] : t["end"]]: t["word_class"] for t in version["tokens"] if t["is_alpha"]
    }
    assert classes["Tisch"] == "presumed_known"
    assert all(t["is_alpha"] or t["word_class"] == "ignore" for t in version["tokens"])


def test_all_attempts_are_stored_and_the_best_is_selected(
    client: TestClient, llm: StubLLM, migrated_settings: Settings
) -> None:
    llm.fixed_text = "Zeitung Zeitung Tisch Haus Wolke Wolke"  # never improves
    text = create_text(client)
    assert len(llm.simplify_requests) == 3  # max_simplify_attempts
    with create_session_factory(migrated_settings)() as db:
        versions = list(db.scalars(select(TextVersion).order_by(TextVersion.attempt)))
        assert [v.attempt for v in versions] == [1, 2, 3]
        assert [v.selected for v in versions] == [True, False, False]  # ties keep the first
        assert all(v.llm_call_id for v in versions)
        assert db.scalar(select(LLMCall).where(LLMCall.task == "simplify_text")) is not None
    assert text["version"]["attempt"] == 1 and text["version"]["coverage"] == pytest.approx(2 / 6)


def test_best_attempt_wins_even_when_it_is_not_the_last(
    client: TestClient, llm: StubLLM, migrated_settings: Settings
) -> None:
    texts = iter(["Tisch Haus Buch Wolke", "Wolke Wolke Wolke Wolke", "Wolke Wolke Tisch Haus"])

    def scripted(req):
        llm.simplify_requests.append(req)
        result = SimplifiedText(title="T", paragraphs=[next(texts)])
        llm._log("simplify_text", req, result, time.monotonic())
        return result

    llm.simplify_text = scripted  # type: ignore[method-assign]
    text = create_text(client)
    assert text["version"]["attempt"] == 1 and text["version"]["coverage"] == 0.75


def test_later_failure_keeps_the_earlier_attempts(client: TestClient, llm: StubLLM) -> None:
    llm.fail_simplify_after = 1
    text = create_text(client)
    assert len(llm.simplify_requests) == 2 and text["version"]["attempt"] == 1


def test_first_attempt_failure_returns_an_error_and_stores_nothing(
    client: TestClient, llm: StubLLM, migrated_settings: Settings
) -> None:
    llm.fail_simplify_after = 0
    assert client.post("/api/texts", json={"text": SOURCE}).status_code == 503
    llm.simplify_error = LLMError("bad output")
    assert client.post("/api/texts", json={"text": SOURCE}).status_code == 502
    with create_session_factory(migrated_settings)() as db:
        assert db.scalar(select(SourceText)) is None
    assert client.get("/api/texts").json() == []


def test_create_text_validation(client: TestClient) -> None:
    assert client.post("/api/texts", json={}).status_code == 422
    assert client.post("/api/texts", json={"url": "http://x", "text": "y"}).status_code == 422
    assert client.post("/api/texts", json={"text": "zu kurz"}).status_code == 422
    assert client.post("/api/texts", json={"text": "wort " * 5000}).status_code == 422


def test_non_german_source_is_a_translation_source(client: TestClient, llm: StubLLM) -> None:
    text = create_text(client, ENGLISH)
    assert text["source_language"] == "other"
    assert llm.simplify_requests[0].source_language == "other"
    assert llm.simplify_requests[0].candidate_lemmas == []


def test_source_length_sets_the_word_limit(client: TestClient, llm: StubLLM) -> None:
    create_text(client, "Tisch und Haus. " * 200)
    assert llm.simplify_requests[0].max_words == 400  # long texts are summarized
    create_text(client, "Tisch und Haus und Buch und Katze. " * 3)
    assert llm.simplify_requests[-1].max_words == 50  # 120% of 21 words, with a floor
    create_text(client, "Tisch und Haus und Buch und Katze. " * 30)
    assert llm.simplify_requests[-1].max_words == 252  # 120% of 210 words


def test_create_text_from_url(client: TestClient, llm: StubLLM) -> None:
    resp = client.post("/api/texts", json={"url": "https://example.org/gesetz"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_url"] == "https://example.org/gesetz"
    assert body["source_title"] == "Neues Gesetz beschlossen"
    assert "Regierung" in body["original"]
    paywalled = client.post("/api/texts", json={"url": "https://example.org/paywall"})
    assert paywalled.status_code == 422 and "HTTP 402" in paywalled.json()["detail"]
    assert client.post("/api/texts", json={"url": "ftp://x/y"}).status_code == 422


def test_generate_text_is_seeded_with_due_lemmas(client: TestClient, llm: StubLLM) -> None:
    # make "Tisch" due: introduce it through a flashcard session
    session = client.post("/api/sessions").json()
    for card in session["cards"]:
        client.post(
            f"/api/sessions/{session['session_id']}/answers",
            json={"exercise_id": card["exercise_id"]},
        )
    client.app.state.now.advance(days=5)  # type: ignore[attr-defined]
    resp = client.post("/api/texts/generate", json={"topic": "Wetter"})
    assert resp.status_code == 201, resp.text
    request = llm.simplify_requests[-1]
    assert request.mode == "generate" and request.topic == "Wetter"
    assert set(request.seed_lemmas) == {"Straße", "fahren", "Fenster", "Apfel"}
    body = resp.json()
    assert body["source_title"] == "Wetter" and body["original"] == ""
    assert body["source_language"] == "generated"


def test_list_and_get_texts(client: TestClient) -> None:
    first = create_text(client)
    second = create_text(client, SOURCE + " Noch ein Satz mit Tisch und Haus.")
    listing = client.get("/api/texts").json()
    assert [t["id"] for t in listing] == [second["id"], first["id"]]
    assert listing[0]["coverage"] >= 0.95 and listing[0]["reading_count"] == 0
    detail = client.get(f"/api/texts/{first['id']}").json()
    assert detail["version"]["body"] == first["version"]["body"]
    assert client.get("/api/texts/999").status_code == 404
    assert client.post("/api/texts/999/reading").status_code == 404
    start(client, first)
    assert client.get("/api/texts").json()[1]["reading_count"] == 1


def test_texts_need_a_learner(migrated_settings: Settings, clock: Clock) -> None:
    app = create_app(migrated_settings, now=clock, analyzer=FakeAnalyzer())
    with TestClient(app) as bare:
        assert bare.get("/api/texts").status_code == 404
        assert bare.post("/api/texts", json={"text": SOURCE}).status_code == 404


# --- gloss -----------------------------------------------------------------------------------


def test_gloss_lexicon_word_needs_no_llm_and_emits_a_lookup(
    client: TestClient, llm: StubLLM, migrated_settings: Settings
) -> None:
    text = create_text(client)
    rid = start(client, text)
    idx = token_index(text, "Tisch")
    gloss = client.post(f"/api/reading/{rid}/gloss", json={"token_index": idx}).json()
    assert llm.gloss_calls == 0
    assert gloss["source"] == "lexicon" and gloss["translation"] == "tavolo"
    assert gloss["lemma"] == "Tisch" and gloss["gender"] == "m" and gloss["plural"] == "Tische"
    assert gloss["item_id"] == "lex:tisch" and gloss["can_optin"] is False
    lookups = events(migrated_settings, item_id="lex:tisch", kind="lookup")
    assert len(lookups) == 1
    assert lookups[0].outcome == "assisted" and lookups[0].facet == "recognition"
    assert lookups[0].evidence_weight == pytest.approx(0.5) and lookups[0].presumed_known
    assert status_of(migrated_settings, "lex:tisch")[0] == "introduced"
    # tapping again in the same session does not add a second event
    client.post(f"/api/reading/{rid}/gloss", json={"token_index": idx})
    assert len(events(migrated_settings, item_id="lex:tisch", kind="lookup")) == 1
    with create_session_factory(migrated_settings)() as db:
        assert len(db.get(ReadingSession, rid).lookups) == 2


def test_gloss_unlisted_word_uses_the_llm_and_is_cached(
    client: TestClient, llm: StubLLM, migrated_settings: Settings
) -> None:
    llm.fixed_text = "Tisch Haus Wolkenkratzer Tisch Haus Buch Katze Stuhl Brot gehen"
    text = create_text(client)
    rid = start(client, text)
    idx = token_index(text, "Wolkenkratzer")
    assert text["version"]["tokens"][idx]["word_class"] == "optin_unlisted"
    first = client.post(f"/api/reading/{rid}/gloss", json={"token_index": idx}).json()
    again = client.post(f"/api/reading/{rid}/gloss", json={"token_index": idx}).json()
    assert llm.gloss_calls == 1
    assert first == again
    assert first["source"] == "llm" and first["item_id"] is None
    assert first["translation"] == "wolkenkratzer (it)" and first["can_optin"] is True
    with create_session_factory(migrated_settings)() as db:
        assert db.scalar(select(GlossCache.lemma)) == "Wolkenkratzer"
        assert db.scalar(select(LLMCall).where(LLMCall.task == "gloss")) is not None
    assert events(migrated_settings, kind="lookup") == []  # no item, no event


def test_gloss_errors(client: TestClient) -> None:
    text = create_text(client)
    rid = start(client, text)
    punct = next(t["i"] for t in text["version"]["tokens"] if not t["is_alpha"])
    assert client.post(f"/api/reading/{rid}/gloss", json={"token_index": punct}).status_code == 422
    assert client.post(f"/api/reading/{rid}/gloss", json={"token_index": 9999}).status_code == 404
    assert client.post("/api/reading/999/gloss", json={"token_index": 0}).status_code == 404
    assert client.post(f"/api/reading/{rid}/gloss", json={"token_index": -1}).status_code == 422


def test_gloss_llm_failure(client: TestClient, llm: StubLLM) -> None:
    llm.fixed_text = "Tisch Haus Wolkenkratzer Tisch Haus Buch Katze Stuhl Brot gehen"
    text = create_text(client)
    rid = start(client, text)
    llm.gloss = lambda req: (_ for _ in ()).throw(LLMUnavailable("down"))  # type: ignore[method-assign]
    idx = token_index(text, "Wolkenkratzer")
    assert client.post(f"/api/reading/{rid}/gloss", json={"token_index": idx}).status_code == 503


# --- opt-in ----------------------------------------------------------------------------------


def test_optin_of_a_listed_word(
    client: TestClient, migrated_settings: Settings, llm: StubLLM
) -> None:
    llm.fixed_text = "Tisch Haus Fenster Apfel Tisch Haus Buch Katze Stuhl Brot gehen"
    text = create_text(client)
    rid = start(client, text)
    # Fenster is a wordlist candidate at A2; Apfel too. Opt-in upgrades the source.
    assert status_of(migrated_settings, "lex:fenster") == ("candidate", "wordlist")
    resp = client.post(
        f"/api/reading/{rid}/optin", json={"token_index": token_index(text, "Fenster")}
    )
    assert resp.status_code == 200 and resp.json() == {
        "item_id": "lex:fenster",
        "label": "das Fenster",
        "status": "candidate",
        "created": False,
    }
    assert status_of(migrated_settings, "lex:fenster") == ("candidate", "optin")
    # known words are left alone
    resp = client.post(
        f"/api/reading/{rid}/optin", json={"token_index": token_index(text, "Tisch")}
    )
    assert resp.json()["status"] == "presumed_known" and resp.json()["created"] is False
    assert status_of(migrated_settings, "lex:tisch")[0] == "presumed_known"


def test_optin_of_a_word_above_the_level(
    client: TestClient, llm: StubLLM, migrated_settings: Settings
) -> None:
    assert client.put("/api/learner", json={"level": "A1"}).status_code == 200
    assert status_of(migrated_settings, "lex:apfel") == ("unseen", None)
    llm.fixed_text = "Tisch Haus Apfel Tisch Haus Buch Katze Stuhl Brot gehen"
    text = create_text(client)
    idx = token_index(text, "Apfel")
    assert text["version"]["tokens"][idx]["word_class"] == "optin"
    rid = start(client, text)
    gloss = client.post(f"/api/reading/{rid}/gloss", json={"token_index": idx}).json()
    assert gloss["source"] == "lexicon" and gloss["can_optin"] is True and llm.gloss_calls == 0
    resp = client.post(f"/api/reading/{rid}/optin", json={"token_index": idx})
    assert resp.json()["status"] == "candidate" and resp.json()["created"] is False
    assert status_of(migrated_settings, "lex:apfel") == ("candidate", "optin")
    # finishing does not downgrade the source to "article"
    client.post(f"/api/reading/{rid}/finish")
    assert status_of(migrated_settings, "lex:apfel") == ("candidate", "optin")


def test_optin_of_an_unlisted_word_creates_a_user_item(
    client: TestClient, llm: StubLLM, migrated_settings: Settings
) -> None:
    llm.fixed_text = "Tisch Haus Wolkenkratzer Tisch Haus Buch Katze Stuhl Brot gehen"
    text = create_text(client)
    rid = start(client, text)
    idx = token_index(text, "Wolkenkratzer")
    resp = client.post(f"/api/reading/{rid}/optin", json={"token_index": idx})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "item_id": "lex:user-wolkenkratzer",
        "label": "das Wolkenkratzer",
        "status": "candidate",
        "created": True,
    }
    assert llm.gloss_calls == 1
    with create_session_factory(migrated_settings)() as db:
        item = db.get(Item, "lex:user-wolkenkratzer")
        assert item.kind == "lemma" and item.source_file == "user" and item.cefr_level == "A2"
        assert item.payload["user_created"] is True and item.payload["lemma"] == "Wolkenkratzer"
        assert item.payload["translations"]["it"] == "wolkenkratzer (it)"
        assert item.payload["pos"] == "noun" and item.suspended is False
    assert status_of(migrated_settings, "lex:user-wolkenkratzer") == ("candidate", "optin")
    # opting in twice reuses the item; the gloss now comes from the lexicon
    again = client.post(f"/api/reading/{rid}/optin", json={"token_index": idx}).json()
    assert again["created"] is False and again["item_id"] == "lex:user-wolkenkratzer"
    gloss = client.post(f"/api/reading/{rid}/gloss", json={"token_index": idx}).json()
    assert gloss["source"] == "lexicon" and gloss["item_id"] == "lex:user-wolkenkratzer"
    assert gloss["can_optin"] is False
    # the user item works in a flashcard session and in later analyses
    session = client.post("/api/sessions").json()
    assert any(c["item_id"] == "lex:user-wolkenkratzer" for c in session["cards"])
    later = create_text(client)
    assert any(t["item_id"] == "lex:user-wolkenkratzer" for t in later["version"]["tokens"])


def test_importer_never_suspends_user_items(
    client: TestClient, llm: StubLLM, migrated_settings: Settings, clock: Clock
) -> None:
    llm.fixed_text = "Tisch Haus Wolkenkratzer Tisch Haus Buch Katze Stuhl Brot gehen"
    text = create_text(client)
    rid = start(client, text)
    client.post(
        f"/api/reading/{rid}/optin", json={"token_index": token_index(text, "Wolkenkratzer")}
    )
    curriculum = load_curriculum(FIXTURES / "curriculum")
    with create_session_factory(migrated_settings)() as db:
        report = import_curriculum(db, curriculum, clock.now)
        db.commit()
        assert report.suspended == 0
        assert db.get(Item, "lex:user-wolkenkratzer").suspended is False
        # a curriculum item that disappears is still suspended
        curriculum.items.pop(0)
        report = import_curriculum(db, curriculum, clock.now)
        db.commit()
        assert report.suspended == 1
        assert db.get(Item, "lex:user-wolkenkratzer").suspended is False


# --- finish ----------------------------------------------------------------------------------


def test_finish_effects(client: TestClient, llm: StubLLM, migrated_settings: Settings) -> None:
    llm.fixed_text = "Tisch Haus Buch Katze Stuhl Brot Fenster Apfel Tisch gehen"
    text = create_text(client)
    rid = start(client, text)
    client.post(f"/api/reading/{rid}/gloss", json={"token_index": token_index(text, "Haus")})
    client.post(f"/api/reading/{rid}/gloss", json={"token_index": token_index(text, "Fenster")})
    resp = client.post(f"/api/reading/{rid}/finish")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # implicit events: every known word except the looked-up one, weight implicit_reading
    implicit = events(migrated_settings, kind="implicit")
    ids = {e.item_id for e in implicit}
    assert ids == {"lex:tisch", "lex:buch", "lex:katze", "lex:stuhl", "lex:brot", "lex:gehen"}
    assert body["implicit_events"] == 6
    assert all(e.outcome == "correct" and e.facet == "recognition" for e in implicit)
    assert all(e.evidence_weight == pytest.approx(0.2) for e in implicit)
    assert all(e.presumed_known for e in implicit)
    assert status_of(migrated_settings, "lex:tisch")[0] == "introduced"
    assert len(events(migrated_settings, item_id="lex:haus", kind="implicit")) == 0
    assert len(events(migrated_settings, item_id="lex:haus", kind="lookup")) == 1
    # article candidates: Apfel was unseen-or-wordlist candidate -> source article
    assert set(body["candidates"]) == {"lex:fenster", "lex:apfel"}
    assert status_of(migrated_settings, "lex:apfel") == ("candidate", "article")
    assert status_of(migrated_settings, "lex:fenster") == ("candidate", "article")
    assert client.post(f"/api/reading/{rid}/finish").status_code == 409
    with create_session_factory(migrated_settings)() as db:
        assert db.get(ReadingSession, rid).finished_at is not None


def test_implicit_events_are_capped_by_frequency(
    client: TestClient, llm: StubLLM, migrated_settings: Settings, clock: Clock
) -> None:
    from app.domain.config import ReadingConfig
    from app.services import reading
    from app.store.models import Learner

    llm.fixed_text = "Tisch Haus Buch Katze Stuhl Brot Mädchen Eltern gehen schön"
    text = create_text(client)
    rid = start(client, text)
    with create_session_factory(migrated_settings)() as db:
        result = reading.finish_reading(
            db, db.get(Learner, 1), rid, clock.now, ReadingConfig(max_implicit_per_text=3)
        )
        zipf = {i.id: i.frequency_zipf for i in db.scalars(select(Item))}
    assert result["implicit_events"] == 3
    credited = [e.item_id for e in events(migrated_settings, kind="implicit")]
    assert len(credited) == 3
    others = {
        "lex:" + w for w in "tisch haus buch katze stuhl brot maedchen eltern gehen schoen".split()
    }
    assert min(zipf[i] for i in credited) >= max(
        zipf[i] for i in others - set(credited) if i in zipf
    )


def test_summary_exercise_flow(
    client: TestClient, llm: StubLLM, migrated_settings: Settings
) -> None:
    llm.fixed_text = "Tisch Haus Buch Katze Stuhl Brot Fenster Apfel Tisch gehen"
    text = create_text(client)
    rid = start(client, text)
    finished = client.post(f"/api/reading/{rid}/finish").json()
    card = finished["exercise"]
    assert finished["session_id"] == f"reading-{rid}"
    assert card["type"] == "production" and card["subtype"] == "summary"
    assert card["status"] == "ready" and "2-4" in card["instructions"]
    assert card["prompt"]["text"] == text["version"]["title"]
    assert 1 <= len(card["item_ids"]) <= 3
    assert all("reference_solutions" not in str(card[k]) for k in card)

    answered = client.post(
        f"/api/sessions/{finished['session_id']}/answers",
        json={"exercise_id": card["exercise_id"], "answer": {"text": "Der Tisch ist im Haus."}},
    )
    assert answered.status_code == 200, answered.text
    result = answered.json()
    assert result["kind"] == "production" and result["outcome"] == "correct"
    assert {i["item_id"] for i in result["items"]} == set(card["item_ids"])
    # evidence weight "free" (1.2) x target weight
    reviews = events(migrated_settings, kind="review", exercise_id=card["exercise_id"])
    assert reviews and reviews[0].evidence_weight == pytest.approx(1.2)
    assert all(e.facet == "production" for e in reviews)
    # answering twice is a conflict
    again = client.post(
        f"/api/sessions/{finished['session_id']}/answers",
        json={"exercise_id": card["exercise_id"], "answer": {"text": "x"}},
    )
    assert again.status_code == 409
    # the exercise appears in no other learner's session and the finish call can't be repeated
    assert client.post(f"/api/reading/{rid}/finish").status_code == 409


def test_summary_targets_prefer_due_or_weak_items(
    client: TestClient, llm: StubLLM, migrated_settings: Settings
) -> None:
    from app.store.models import ItemMemory

    llm.fixed_text = "Tisch Haus Buch Katze Stuhl Brot gehen"
    text = create_text(client)
    rid = start(client, text)
    with create_session_factory(migrated_settings)() as db:
        for item_id, mastery in (("lex:tisch", 0.2), ("lex:haus", 0.9), ("lex:buch", 0.3)):
            db.add(
                ItemMemory(
                    learner_id=1,
                    item_id=item_id,
                    facet="recognition",
                    fsrs_card=None,
                    due=None,
                    mastery=mastery,
                    n_effective=1.0,
                    tag_error_counts={},
                    last_event_id=0,
                    projection_version="x",
                )
            )
        db.commit()
    card = client.post(f"/api/reading/{rid}/finish").json()["exercise"]
    assert card["item_ids"] == ["lex:tisch", "lex:buch"]  # weak ones, weakest first


# --- replay ----------------------------------------------------------------------------------


def test_reading_events_replay_to_the_same_projection(
    client: TestClient, llm: StubLLM, migrated_settings: Settings, monkeypatch
) -> None:
    llm.fixed_text = "Tisch Haus Buch Katze Stuhl Brot Fenster Apfel Tisch gehen"
    text = create_text(client)
    rid = start(client, text)
    client.post(f"/api/reading/{rid}/gloss", json={"token_index": token_index(text, "Haus")})
    finished = client.post(f"/api/reading/{rid}/finish").json()
    card = finished["exercise"]
    client.post(
        f"/api/sessions/{finished['session_id']}/answers",
        json={"exercise_id": card["exercise_id"], "answer": {"text": "Das Haus."}},
    )
    before = snapshot(migrated_settings)
    assert before
    monkeypatch.setenv("LLMLL_DATABASE_URL", migrated_settings.database_url)
    get_settings.cache_clear()
    try:
        assert cli_main(["replay"]) == 0
    finally:
        get_settings.cache_clear()
    assert snapshot(migrated_settings) == before


def test_start_reading_resumes_unfinished_session(client: TestClient) -> None:
    text = create_text(client)
    first = start(client, text)
    assert start(client, text) == first  # e.g. a page reload
    assert client.post(f"/api/reading/{first}/finish").status_code == 200
    assert start(client, text) != first  # a finished session is not reused
