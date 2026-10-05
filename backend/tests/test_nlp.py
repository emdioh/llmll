import httpx
import pytest

from app.domain.words import (
    LearnerView,
    LexiconWord,
    classify,
    coverage,
    pick_implicit,
    sentence_around,
)
from app.nlp.analyzer import FakeAnalyzer, SpacyAnalyzer
from app.nlp.compounds import split_compound
from app.nlp.extract import ExtractionFailed, fetch_article, source_language

from .conftest import FIXTURES

KNOWN = {
    "Klima": "noun",
    "Schutz": "noun",
    "Gesetz": "noun",
    "Haus": "noun",
    "Tür": "noun",
    "Arbeit": "noun",
    "Zeit": "noun",
    "gehen": "verb",
    "Tag": "noun",
}


# --- compounds -------------------------------------------------------------------------------


def test_split_compound_with_linking_elements() -> None:
    assert split_compound("Klimaschutzgesetz", KNOWN) == ["Klima", "Schutz", "Gesetz"]
    assert split_compound("Arbeitszeit", KNOWN) == ["Arbeit", "Zeit"]
    assert split_compound("Haustür", KNOWN) == ["Haus", "Tür"]
    assert split_compound("haustür", KNOWN) == ["Haus", "Tür"]  # case-insensitive
    assert split_compound("Tagesarbeit", KNOWN) == ["Tag", "Arbeit"]  # linking "es"


def test_split_compound_negative_cases() -> None:
    assert split_compound("Klimaschutzverordnung", KNOWN) is None  # one part unknown
    assert split_compound("Haus", KNOWN) is None  # not a compound
    assert split_compound("Hausgehen", KNOWN) is None  # the head must be a noun
    assert split_compound("Zeitung", KNOWN) is None  # leftover "ung"
    assert split_compound("Am", KNOWN) is None  # too short
    assert split_compound("Tür", {"Tü": "noun", "r": "noun"}) is None  # parts under 3 letters


def test_split_compound_prefers_fewest_parts() -> None:
    known = {"Haus": "noun", "Tür": "noun", "Haustür": "noun"}
    assert split_compound("Haustürhaus", known) == ["Haustür", "Haus"]


# --- classification --------------------------------------------------------------------------


def view() -> LearnerView:
    return LearnerView.build(
        [
            LexiconWord("lex:tisch", "Tisch", "A1", "introduced", "noun"),
            LexiconWord("lex:haus", "Haus", "A1", "presumed_known", "noun"),
            LexiconWord("lex:tuer", "Tür", "A1", "presumed_known", "noun"),
            LexiconWord("lex:fenster", "Fenster", "A2", "candidate", "noun"),
            LexiconWord("lex:apfel", "Apfel", "A2", "unseen", "noun"),
            LexiconWord("lex:regierung", "Regierung", "B1", "unseen", "noun"),
            LexiconWord("lex:bank#money", "Bank", "A1", "unseen", "noun"),
            LexiconWord("lex:bank#bench", "Bank", "A1", "introduced", "noun"),
        ]
    )


@pytest.mark.parametrize(
    ("lemma", "kwargs", "expected", "item"),
    [
        ("Tisch", {}, "known", "lex:tisch"),
        ("tisch", {}, "known", "lex:tisch"),  # case-insensitive fallback
        ("Haus", {}, "presumed_known", "lex:haus"),
        ("Fenster", {}, "auto_candidate", "lex:fenster"),
        ("Apfel", {}, "auto_candidate", "lex:apfel"),
        ("Regierung", {}, "optin", "lex:regierung"),
        ("Bank", {}, "known", "lex:bank#bench"),  # homograph: best status wins
        ("Berlin", {"is_propn": True}, "ignore", None),
        ("Merkel", {"ent_type": "PER"}, "ignore", None),
        ("Siemens", {"ent_type": "ORG"}, "ignore", None),
        ("die", {"pos": "DET"}, "ignore", None),
        ("2024", {"is_alpha": False, "pos": "NUM"}, "ignore", None),
        ("Zeitung", {}, "optin_unlisted", None),
        ("Haustür", {}, "ignore_compound", None),
    ],
)
def test_classify(lemma, kwargs, expected, item) -> None:
    params = {"pos": "NOUN", "is_propn": False, **kwargs}
    result = classify(lemma, params.pop("pos"), params.pop("is_propn"), view(), "A2", **params)
    assert result.word_class == expected
    assert result.item_id == item
    if expected == "ignore_compound":
        assert result.parts == ("Haus", "Tür")


def test_classify_uses_surface_form_when_the_lemma_is_unknown() -> None:
    result = classify("Tischs", "NOUN", False, view(), "A2", surface="Tisch")
    assert result.word_class == "known"


def test_coverage_counts_alphabetic_non_ignored_words() -> None:
    classes = ["known", "presumed_known", "ignore_compound", "optin", "auto_candidate", "ignore"]
    alpha = [True, True, True, True, True, True]
    assert coverage(alpha, classes) == pytest.approx(3 / 5)
    # punctuation and non-alphabetic tokens are not counted, even with a counted class
    assert coverage([True, False, True], ["known", "optin", "optin_unlisted"]) == 0.5
    assert coverage([True], ["ignore"]) == 1.0
    assert coverage([], []) == 1.0


def test_pick_implicit_orders_by_frequency_and_caps() -> None:
    freq = {"a": 5.0, "b": 6.0, "c": 4.0, "d": 6.0}
    assert pick_implicit(freq, [], 3) == ["b", "d", "a"]
    assert pick_implicit(freq, ["b"], 2) == ["d", "a"]
    assert pick_implicit(freq, [], 0) == []


def test_sentence_around() -> None:
    text = "Das ist gut. Der Tisch steht hier! Und noch ein Satz."
    start = text.index("Tisch")
    assert sentence_around(text, start, start + 5) == "Der Tisch steht hier!"
    assert sentence_around(text, 0, 3) == "Das ist gut."
    assert sentence_around("ohne punkt", 0, 4) == "ohne punkt"


# --- analyzer --------------------------------------------------------------------------------


def test_fake_analyzer() -> None:
    tokens = FakeAnalyzer().analyze("Der Tisch, 2024!")
    assert [t.text for t in tokens] == ["Der", "Tisch", ",", "2024", "!"]
    assert [t.is_alpha for t in tokens] == [True, True, False, False, False]
    assert tokens[1].start == 4 and tokens[1].end == 9 and tokens[1].lemma == "Tisch"
    assert [t.i for t in tokens] == [0, 1, 2, 3, 4]


def test_spacy_analyzer_reports_a_missing_model() -> None:
    from app.nlp.analyzer import AnalyzerUnavailable

    with pytest.raises(AnalyzerUnavailable, match="not installed"):
        SpacyAnalyzer("de_no_such_model").analyze("Hallo")


# --- extraction ------------------------------------------------------------------------------


def transport(handler):
    return httpx.MockTransport(handler)


def test_fetch_article_extracts_title_and_text() -> None:
    html = (FIXTURES / "article.html").read_bytes()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=html, headers={"content-type": "text/html"})

    article = fetch_article("https://example.org/gesetz", transport(handler))
    assert article.title == "Neues Gesetz beschlossen"
    assert "Die Regierung hat gestern ein neues Gesetz beschlossen" in article.text
    assert "Impressum" not in article.text and "Cookies" not in article.text
    assert article.url == "https://example.org/gesetz"
    assert "Mozilla" in seen[0].headers["user-agent"]
    assert source_language(article.text) == "de"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(403, text="forbidden"), "HTTP 403"),
        (httpx.Response(200, text="<html><body><p>Kurz.</p></body></html>"), "No article text"),
        (httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 10)), "larger than 2 MB"),
    ],
)
def test_fetch_article_failures(response: httpx.Response, reason: str) -> None:
    with pytest.raises(ExtractionFailed, match=reason):
        fetch_article("https://example.org/x", transport(lambda _r: response))


def test_fetch_article_network_errors_and_bad_urls() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(ExtractionFailed, match="too long"):
        fetch_article("https://example.org/x", transport(boom))
    with pytest.raises(ExtractionFailed, match="http"):
        fetch_article("file:///etc/passwd")
    with pytest.raises(ExtractionFailed, match="http"):
        fetch_article("not a url")


def test_source_language_heuristic() -> None:
    assert (
        source_language("Die Regierung hat gestern ein neues Gesetz beschlossen und es ist gut.")
        == "de"
    )
    assert (
        source_language("The government passed a new law yesterday and people are happy.")
        == "other"
    )
    assert source_language("Il governo ha approvato ieri una nuova legge per le città.") == "other"


def test_check_public_host_blocks_private_addresses() -> None:
    from app.nlp.extract import ExtractionFailed, check_public_host

    for address in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fd00::1"):
        with pytest.raises(ExtractionFailed):
            check_public_host("internal.example", resolve=lambda _h, a=address: [a])
    check_public_host("news.example", resolve=lambda _h: ["93.184.216.34"])
