"""Word classification and lexical coverage of a text (design: docs/design/M3.md §2). Pure."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.curriculum.schema import CEFR_LEVELS
from app.nlp.compounds import split_compound

WordClass = Literal[
    "ignore",
    "known",
    "presumed_known",
    "auto_candidate",
    "optin",
    "optin_unlisted",
    "ignore_compound",
]

KNOWN_STATUSES = ("introduced", "presumed_known")
COVERED = ("known", "presumed_known", "ignore_compound")
IGNORED_POS = ("PUNCT", "NUM", "SYM", "SPACE")
IGNORED_ENTITIES = ("PER", "LOC", "ORG")
STATUS_RANK = {"introduced": 0, "presumed_known": 1, "candidate": 2, "unseen": 3}


@dataclass(frozen=True)
class LexiconWord:
    """One lexicon item as the learner sees it."""

    item_id: str
    lemma: str
    level: str
    status: str
    pos: str = ""

    def sort_key(self) -> tuple[int, int, str]:
        return (STATUS_RANK.get(self.status, 9), CEFR_LEVELS.index(self.level), self.item_id)


@dataclass(frozen=True)
class Classification:
    word_class: WordClass
    item_id: str | None = None
    parts: tuple[str, ...] = ()


@dataclass(frozen=True)
class LearnerView:
    """Lexicon lemmas with the learner's status; homographs keep their best entry first."""

    by_lemma: Mapping[str, tuple[LexiconWord, ...]]
    folded: Mapping[str, tuple[LexiconWord, ...]] = field(default_factory=dict)

    @classmethod
    def build(cls, words: Iterable[LexiconWord]) -> "LearnerView":
        exact: dict[str, list[LexiconWord]] = {}
        folded: dict[str, list[LexiconWord]] = {}
        for word in words:
            exact.setdefault(word.lemma, []).append(word)
            folded.setdefault(word.lemma.casefold(), []).append(word)
        return cls(
            {k: tuple(sorted(v, key=LexiconWord.sort_key)) for k, v in exact.items()},
            {k: tuple(sorted(v, key=LexiconWord.sort_key)) for k, v in folded.items()},
        )

    def lookup(self, lemma: str) -> LexiconWord | None:
        """Exact lemma first, then case-insensitive."""
        found = self.by_lemma.get(lemma) or self.folded.get(lemma.casefold())
        return found[0] if found else None

    def known_lemmas(self) -> dict[str, str]:
        """Lemma -> part of speech of the items the learner knows (compound parts)."""
        return {
            word.lemma: word.pos
            for words in self.by_lemma.values()
            for word in words
            if word.status in KNOWN_STATUSES
        }


def classify(
    lemma: str,
    pos: str,
    is_propn: bool,
    learner_view: LearnerView,
    current_level: str,
    *,
    ent_type: str | None = None,
    surface: str | None = None,
    is_alpha: bool = True,
) -> Classification:
    """Class of one token. `surface` is the word as written, tried when the lemma is not listed."""
    if not is_alpha or pos in IGNORED_POS or is_propn:
        return Classification("ignore")
    entry = learner_view.lookup(lemma) or (learner_view.lookup(surface) if surface else None)
    if entry is not None:
        if entry.status == "introduced":
            return Classification("known", entry.item_id)
        if entry.status == "presumed_known":
            return Classification("presumed_known", entry.item_id)
        if CEFR_LEVELS.index(entry.level) <= CEFR_LEVELS.index(current_level):
            return Classification("auto_candidate", entry.item_id)
        return Classification("optin", entry.item_id)
    # Not listed: names and articles are not vocabulary to learn.
    if ent_type in IGNORED_ENTITIES or pos == "DET":
        return Classification("ignore")
    parts = split_compound(lemma, learner_view.known_lemmas())
    if parts is None and surface and surface != lemma:
        parts = split_compound(surface, learner_view.known_lemmas())
    if parts is not None:
        return Classification("ignore_compound", None, tuple(parts))
    return Classification("optin_unlisted")


def coverage(is_alpha: Sequence[bool], classes: Sequence[str]) -> float:
    """Share of the counted words (alphabetic, not ignored) the learner covers: known words,
    presumed-known words and compounds made of known words. 1.0 when nothing is counted."""
    counted = [c for a, c in zip(is_alpha, classes, strict=True) if a and c != "ignore"]
    if not counted:
        return 1.0
    return sum(c in COVERED for c in counted) / len(counted)


def sentence_around(text: str, start: int, end: int, limit: int = 300) -> str:
    """The sentence of `text` that contains the span, trimmed to about `limit` characters."""
    left = max(text.rfind(c, 0, start) for c in ".!?\n")
    right_candidates = [i for c in ".!?\n" if (i := text.find(c, end)) != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    sentence = text[left + 1 : right].strip()
    if len(sentence) > limit:
        offset = start - (left + 1)
        lo = max(0, offset - limit // 2)
        sentence = sentence[lo : lo + limit].strip()
    return sentence


def pick_implicit(
    frequencies: Mapping[str, float], looked_up: Iterable[str], cap: int
) -> list[str]:
    """Items to credit with implicit reading evidence: those not looked up, most frequent first
    (ties by id), at most `cap`. `frequencies` maps item id -> zipf frequency."""
    skipped = set(looked_up)
    ranked = sorted(
        (i for i in frequencies if i not in skipped), key=lambda i: (-frequencies[i], i)
    )
    return ranked[: max(cap, 0)]
