"""Flashcard answer checking and display helpers."""

import unicodedata

ARTICLES = {"m": "der", "f": "die", "n": "das", "pl": "die"}
UMLAUT_ASCII = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})
MIN_TYPO_LENGTH = 4


def article_for(gender: str | None, plural_only: bool = False) -> str | None:
    if plural_only:
        return ARTICLES["pl"]
    return ARTICLES.get(gender) if gender else None


def display_form(lemma: str, gender: str | None, plural_only: bool = False) -> str:
    article = article_for(gender, plural_only)
    return f"{article} {lemma}" if article else lemma


def normalize(text: str) -> str:
    """NFC, trimmed, collapsed whitespace, casefolded (which maps ß to ss)."""
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def check_gap(answer: str, accepted: list[str]) -> str:
    """Check the filler of a cloze gap: `correct`, `assisted` (umlaut spelled ae/oe/ue) or `error`.

    Case and surrounding punctuation are ignored. There is no typo tolerance: in a grammar gap a
    one-letter difference is usually the error being tested (den / dem).
    """
    given = normalize(answer).strip(" .,;:!?")
    expected = [normalize(a).strip(" .,;:!?") for a in accepted]
    if given in expected:
        return "correct"
    if given in {e.translate(UMLAUT_ASCII) for e in expected}:
        return "assisted"
    return "error"


def check_recognition(choice_index: int | None, correct_index: int) -> str:
    return "correct" if choice_index == correct_index else "error"


def _match_word(answer: str, expected: str) -> str:
    """Compare normalized words: `exact`, `spelling` (umlaut alternative / typo) or `wrong`."""
    if answer == expected:
        return "exact"
    if answer == expected.translate(UMLAUT_ASCII):
        return "spelling"
    if len(expected) >= MIN_TYPO_LENGTH and levenshtein(answer, expected) == 1:
        return "spelling"
    return "wrong"


def check_production(
    answer: str, expected_lemma: str, expected_gender: str | None
) -> tuple[str, list[str]]:
    """Check a production answer.

    `expected_gender` is `m`/`f`/`n`, `pl` for plural-only nouns, or None for non-nouns.
    Returns `(outcome, diagnostic_tags)`; the hint penalty is applied by the caller.
    """
    given = normalize(answer)
    lemma = normalize(expected_lemma)
    if expected_gender is None:
        match = _match_word(given, lemma)
        if match == "exact":
            return "correct", []
        return ("assisted", ["spelling"]) if match == "spelling" else ("error", [])

    article = ARTICLES[expected_gender]
    first, _, rest = given.partition(" ")
    if first in ARTICLES.values() and rest:
        given_article, noun = first, rest
    else:
        given_article, noun = None, given

    match = _match_word(noun, lemma)
    if given_article is None:
        # Whole answer may itself start with something that is not an article.
        return "error", ["article_missing"] if match != "wrong" else []
    tags: list[str] = []
    if match == "wrong":
        return "error", tags
    if given_article != article:
        tags.append("gender")
        if match == "spelling":
            tags.append("spelling")
        return "error", tags
    return ("correct", []) if match == "exact" else ("assisted", ["spelling"])
