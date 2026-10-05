import httpx
import pytest

from app.llm.fake import FakeLLMClient, first_paragraph
from app.nlp.languagetool import LanguageToolClient

from .llm_helpers import GRAMMAR, exercise_request, explain_request, grade_request


def test_fake_generate_is_template_based() -> None:
    gen = FakeLLMClient().generate_exercise(exercise_request())
    assert gen.prompt == "Il tavolo."
    assert gen.reference_solutions == ["Der Tisch."]
    assert [g.item_id for g in gen.glossary] == ["lex:fenster"]  # new lemmas only
    assert gen.glossary[0].de == "das Fenster" and gen.glossary[0].translation == "finestra"
    assert [(t.item_id, t.weight) for t in gen.targets] == [
        ("gram:cases", 1.0),
        ("lex:fenster", 0.3),
    ]


def test_fake_grade_accepts_normalized_reference() -> None:
    fake = FakeLLMClient()
    ok = fake.grade_sentence(grade_request("  der   tisch "))
    assert ok.overall == "correct" and ok.errors == []
    assert ok.correct_uses == ["gram:cases", "lex:fenster"]


def test_fake_grade_mismatch_is_one_major_error_on_first_target() -> None:
    result = FakeLLMClient().grade_sentence(grade_request("Das Tisch"))
    assert result.overall == "major_errors" and len(result.errors) == 1
    error = result.errors[0]
    assert (error.severity, error.confidence, error.item_id) == ("major", 0.9, "gram:cases")
    assert (error.start, error.end, error.original) == (0, 9, "Das Tisch")
    assert result.corrected_sentence == "Der Tisch."
    assert FakeLLMClient().grade_sentence(grade_request("   ")).overall == "off_task"


def test_fake_explain_returns_first_paragraph() -> None:
    out = FakeLLMClient().explain(explain_request())
    assert out.markdown == "In tedesco: *der*, *die*, *das*."
    assert out.examples[0].de == "Der Tisch."
    assert first_paragraph("## Titolo\n\nPrimo.\n\nSecondo.") == "Primo."
    assert first_paragraph("## Titolo\nPrimo.\n\nSecondo.") == "Primo."
    assert GRAMMAR.reference_it


# --- LanguageTool -----------------------------------------------------------------------------


def lt_client(handler) -> LanguageToolClient:
    return LanguageToolClient("http://lt:8010/", transport=httpx.MockTransport(handler))


def test_languagetool_parses_matches() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "matches": [
                    {
                        "offset": 4,
                        "length": 5,
                        "message": "Falsche Form",
                        "rule": {"id": "DE_AGREEMENT", "category": {"id": "GRAMMAR", "name": "G"}},
                        "replacements": [{"value": v} for v in ("a", "b", "c", "d")],
                    }
                ]
            },
        )

    matches = lt_client(handler).check("Ich Tisch")
    assert seen["url"] == "http://lt:8010/v2/check"
    assert "language=de-DE" in seen["body"] and "text=Ich+Tisch" in seen["body"]
    assert matches is not None and len(matches) == 1
    m = matches[0]
    assert (m.offset, m.length, m.rule_id, m.category) == (4, 5, "DE_AGREEMENT", "GRAMMAR")
    assert m.message == "Falsche Form" and m.replacements == ["a", "b", "c"]


def test_languagetool_no_matches() -> None:
    client = lt_client(lambda r: httpx.Response(200, json={"matches": []}))
    assert client.check("Hallo") == []


@pytest.mark.parametrize("failure", ["connect", "status", "json"])
def test_languagetool_unreachable_returns_none(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "connect":
            raise httpx.ConnectError("down", request=request)
        if failure == "status":
            return httpx.Response(500)
        return httpx.Response(200, text="not json")

    assert lt_client(handler).check("Hallo") is None
