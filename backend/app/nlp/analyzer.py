"""Morphological analysis of German text (design: docs/design/M3.md §1.1).

`Analyzer` is the injectable interface: `SpacyAnalyzer` in production, `FakeAnalyzer` in tests.
"""

import re
import threading
from typing import Any, Protocol

from pydantic import BaseModel

MODEL_NAME = "de_core_news_md"


class Token(BaseModel):
    i: int
    text: str
    start: int
    end: int
    lemma: str
    pos: str
    is_alpha: bool
    is_punct: bool
    is_propn: bool
    ent_type: str | None = None


class AnalyzerUnavailable(Exception):
    """The analyzer cannot run (spaCy model missing)."""


class Analyzer(Protocol):
    def analyze(self, text: str) -> list[Token]: ...


class SpacyAnalyzer:
    """spaCy `de_core_news_md`, loaded lazily on first use and cached."""

    def __init__(self, model: str = MODEL_NAME) -> None:
        self._model = model
        self._nlp: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._nlp is None:
                import spacy

                try:
                    # The parser is the slowest component and nothing here needs it.
                    self._nlp = spacy.load(self._model, exclude=["parser"])
                except OSError as exc:
                    raise AnalyzerUnavailable(
                        f"spaCy model {self._model} not installed: run `uv sync`"
                    ) from exc
            return self._nlp

    def analyze(self, text: str) -> list[Token]:
        doc = self._load()(text)
        tokens: list[Token] = []
        for tok in doc:
            if tok.is_space:
                continue
            tokens.append(
                Token(
                    i=len(tokens),
                    text=tok.text,
                    start=tok.idx,
                    end=tok.idx + len(tok.text),
                    lemma=tok.lemma_ or tok.text,
                    pos=tok.pos_,
                    is_alpha=tok.is_alpha,
                    is_punct=tok.is_punct,
                    is_propn=tok.pos_ == "PROPN",
                    ent_type=tok.ent_type_ or None,
                )
            )
        return tokens


_WORD_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class FakeAnalyzer:
    """Regex tokenizer with identity lemmas, for tests that do not need spaCy."""

    def analyze(self, text: str) -> list[Token]:
        tokens: list[Token] = []
        for m in _WORD_RE.finditer(text):
            word = m.group()
            alpha = word.isalpha()
            tokens.append(
                Token(
                    i=len(tokens),
                    text=word,
                    start=m.start(),
                    end=m.end(),
                    lemma=word,
                    pos="X" if alpha else ("NUM" if word.isdigit() else "PUNCT"),
                    is_alpha=alpha,
                    is_punct=not word[0].isalnum() and word[0] != "_",
                    is_propn=False,
                )
            )
        return tokens
