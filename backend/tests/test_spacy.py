import importlib.util

import pytest

from app.domain.words import LearnerView, LexiconWord, classify
from app.nlp.analyzer import SpacyAnalyzer

pytestmark = [
    pytest.mark.spacy,
    pytest.mark.skipif(
        importlib.util.find_spec("de_core_news_md") is None,
        reason="spaCy model not installed: run `uv sync`",
    ),
]


@pytest.fixture(scope="module")
def analyzer() -> SpacyAnalyzer:
    return SpacyAnalyzer()


def test_lemmas_and_offsets(analyzer: SpacyAnalyzer) -> None:
    text = "Die Kinder gehen gestern mit neuen Büchern in Berlin spazieren."
    tokens = analyzer.analyze(text)
    by_text = {t.text: t for t in tokens}
    assert by_text["Kinder"].lemma == "Kind"
    assert by_text["gehen"].lemma == "gehen"
    assert by_text["Büchern"].lemma == "Buch"
    assert by_text["neuen"].lemma == "neu"
    assert by_text["Berlin"].is_propn
    assert by_text["."].is_punct and not by_text["."].is_alpha
    assert all(text[t.start : t.end] == t.text for t in tokens)
    assert [t.i for t in tokens] == list(range(len(tokens)))


def test_classification_with_real_lemmas(analyzer: SpacyAnalyzer) -> None:
    view = LearnerView.build(
        [
            LexiconWord("lex:buch", "Buch", "A1", "presumed_known", "noun"),
            LexiconWord("lex:schutz", "Schutz", "A2", "presumed_known", "noun"),
            LexiconWord("lex:gesetz", "Gesetz", "A2", "presumed_known", "noun"),
            LexiconWord("lex:klima", "Klima", "A2", "presumed_known", "noun"),
        ]
    )
    classes = {
        t.text: classify(
            t.lemma, t.pos, t.is_propn, view, "A2", ent_type=t.ent_type, surface=t.text
        ).word_class
        for t in analyzer.analyze("Das Klimaschutzgesetz steht in den Büchern von Berlin.")
    }
    assert classes["Büchern"] == "presumed_known"
    assert classes["Klimaschutzgesetz"] == "ignore_compound"
    assert classes["Berlin"] == "ignore"
    assert classes["den"] == "ignore"  # determiner
