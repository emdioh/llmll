"""Planning of production exercises for a session (design: docs/design/M2.md §2.1). Pure."""

from collections.abc import Sequence
from dataclasses import dataclass

PRIMARY_WEIGHT = 1.0
SECONDARY_WEIGHT = 0.5
NEW_LEMMA_WEIGHT = 0.3
MAX_SECONDARY = 3
MAX_NEW_LEMMAS_PER_EXERCISE = 3
WEAK_MASTERY = 0.5
ROTATION = ("guided", "transform", "translation")


@dataclass(frozen=True)
class Candidate:
    item_id: str
    kind: str  # lemma | grammar | construction
    mastery: float | None = None
    retrievability: float | None = None


@dataclass(frozen=True)
class PlannedTarget:
    item_id: str
    kind: str
    weight: float
    new: bool
    role: str  # primary | secondary


@dataclass(frozen=True)
class PlannedExercise:
    subtype: str  # translation | guided | transform
    targets: tuple[PlannedTarget, ...]


def _by_retrievability(candidates: Sequence[Candidate]) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda c: (c.retrievability if c.retrievability is not None else 1.0, c.item_id),
    )


def plan_production(
    slots: int,
    remediation: Sequence[Candidate],
    due: Sequence[Candidate],
    new_grammar: Sequence[Candidate],
    new_lemmas: Sequence[Candidate],
    grammar_budget: int,
    lemma_budget: int,
    ahead: Sequence[Candidate] = (),
) -> list[PlannedExercise]:
    """Up to `slots` exercises.

    Primary targets (grammar points / constructions) come in priority order: remediation queue,
    due memories (lowest retrievability first), then new items within `grammar_budget` (at most
    one new grammar item per exercise, as there is one primary target), then `ahead` memories
    (not yet due, practised ahead of schedule; lowest retrievability first). Secondary targets are
    lemmas: remediation lemmas first, then new lemmas within `lemma_budget`. A slot with no
    grammar-like primary falls back to a lemma primary; with nothing to practise it is skipped.
    """
    used: set[str] = set()

    def grammar_like(c: Candidate) -> bool:
        return c.kind != "lemma"

    primaries: list[tuple[Candidate, bool]] = [(c, False) for c in remediation if grammar_like(c)]
    primaries += [(c, False) for c in _by_retrievability([c for c in due if grammar_like(c)])]
    primaries += [(c, True) for c in new_grammar[: max(grammar_budget, 0)] if grammar_like(c)]
    primaries += [(c, False) for c in _by_retrievability([c for c in ahead if grammar_like(c)])]
    remediation_lemmas = [c for c in remediation if not grammar_like(c)]
    lemma_pool = list(new_lemmas)
    lemmas_left = max(lemma_budget, 0)

    exercises: list[PlannedExercise] = []
    primary_iter = iter(primaries)
    for slot in range(slots):
        primary: Candidate | None = None
        primary_is_new = False
        while primary is None:
            nxt = next(primary_iter, None)
            if nxt is None:
                break
            if nxt[0].item_id not in used:
                primary, primary_is_new = nxt
        secondary: list[tuple[Candidate, bool]] = []
        while remediation_lemmas and len(secondary) < MAX_SECONDARY:
            cand = remediation_lemmas.pop(0)
            if cand.item_id not in used:
                secondary.append((cand, False))
        new_count = 0
        while (
            lemma_pool
            and lemmas_left > 0
            and len(secondary) < MAX_SECONDARY
            and new_count < MAX_NEW_LEMMAS_PER_EXERCISE
        ):
            cand = lemma_pool.pop(0)
            if cand.item_id in used:
                continue
            secondary.append((cand, True))
            lemmas_left -= 1
            new_count += 1
        if primary is None:
            if not secondary:
                continue
            primary, primary_is_new = secondary.pop(0)
        targets = [
            PlannedTarget(primary.item_id, primary.kind, PRIMARY_WEIGHT, primary_is_new, "primary")
        ]
        for cand, is_new in secondary:
            targets.append(
                PlannedTarget(
                    cand.item_id,
                    cand.kind,
                    NEW_LEMMA_WEIGHT if is_new else SECONDARY_WEIGHT,
                    is_new,
                    "secondary",
                )
            )
        used.update(t.item_id for t in targets)
        weak = primary.mastery is None or primary.mastery < WEAK_MASTERY
        subtype = (
            "translation"
            if primary_is_new or weak or primary.kind == "lemma"
            else ROTATION[slot % len(ROTATION)]
        )
        exercises.append(PlannedExercise(subtype, tuple(targets)))
    return exercises
