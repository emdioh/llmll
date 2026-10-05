"""Splitting German noun compounds into known lexicon lemmas (design: docs/design/M3.md §1.2)."""

from collections.abc import Mapping
from functools import cache

LINKING = ("", "s", "es", "n", "en", "e")
MIN_PART = 3


def split_compound(word: str, known_lemmas: Mapping[str, str]) -> list[str] | None:
    """Split `word` into known lemmas, or return `None`.

    `known_lemmas` maps a lemma (as written in the curriculum) to its part of speech. Parts are
    matched case-insensitively and may be joined by the linking elements `s`, `es`, `n`, `en`, `e`.
    Every part has at least 3 letters, the last part (the head) must be a noun, and the result has
    at least two parts. The parts are returned as written in `known_lemmas`; the split with the
    fewest parts wins.
    """
    index = {lemma.casefold(): (lemma, pos) for lemma, pos in known_lemmas.items()}
    text = word.casefold()
    if len(text) < 2 * MIN_PART:
        return None

    @cache
    def best(start: int) -> tuple[str, ...] | None:
        """Shortest split of `text[start:]` into parts, the last one being a noun."""
        rest = text[start:]
        entry = index.get(rest)
        if entry is not None and entry[1] == "noun" and len(rest) >= MIN_PART:
            return (entry[0],)
        result: tuple[str, ...] | None = None
        for end in range(start + MIN_PART, len(text) - MIN_PART + 1):
            for link in LINKING:
                stem_end = end - len(link)
                if stem_end - start < MIN_PART or text[stem_end:end] != link:
                    continue
                entry = index.get(text[start:stem_end])
                if entry is None:
                    continue
                tail = best(end)
                if tail is not None and (result is None or 1 + len(tail) < len(result)):
                    result = (entry[0], *tail)
        return result

    parts = best(0)
    if parts is None or len(parts) < 2:
        return None
    return list(parts)
