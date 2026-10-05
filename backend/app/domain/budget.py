"""New-item budget and candidate queue (R§7.4)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.curriculum.schema import CEFR_LEVELS
from app.domain.config import LearnerSettings

SOURCE_PRIORITY = {"optin": 0, "article": 1, "wordlist": 2}
READY_STATUSES = ("introduced", "presumed_known")


@dataclass(frozen=True)
class NewItemBudget:
    lemmas: int
    grammar: int


def new_item_budget(
    introduced_lemmas_7d: int,
    introduced_grammar_7d: int,
    backlog: int,
    settings: LearnerSettings,
) -> NewItemBudget:
    lemmas = max(settings.weekly_new_lemmas - introduced_lemmas_7d, 0)
    grammar = max(settings.weekly_new_grammar - introduced_grammar_7d, 0)
    if backlog > 2 * settings.review_cap:
        return NewItemBudget(0, 0)
    if backlog > settings.review_cap:
        return NewItemBudget(lemmas // 2, grammar // 2)
    return NewItemBudget(lemmas, grammar)


@dataclass(frozen=True)
class QueueItem:
    """Curriculum side of a queue entry; `order` is the curriculum order of grammar-like items."""

    item_id: str
    kind: str  # lemma | grammar | construction
    level: str
    frequency_zipf: float | None = None
    order: int = 0
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class LearnerItemView:
    status: str
    candidate_source: str | None = None


def queue_source(kind: str, view: LearnerItemView, max_level: str | None, level: str) -> str | None:
    """Why an item is queued (`optin|article|wordlist`), or None when it is not a candidate.

    Candidates are items with status `candidate`. Grammar points and constructions are also
    implicit candidates (source `wordlist`) while `unseen` and at or below `max_level`.
    """
    if view.status == "candidate":
        return view.candidate_source or "wordlist"
    if (
        view.status == "unseen"
        and kind != "lemma"
        and max_level is not None
        and CEFR_LEVELS.index(level) <= CEFR_LEVELS.index(max_level)
    ):
        return "wordlist"
    return None


def candidate_queue(
    learner_items: Mapping[str, LearnerItemView],
    items: Sequence[QueueItem],
    now: datetime,
    *,
    max_level: str | None = None,
) -> list[str]:
    """Item ids to introduce next, best first.

    Order: source (`optin > article > wordlist`), level, then curriculum order (grammar-like
    items) or `frequency_zipf` descending (lemmas), then id. Items with an unmet prerequisite
    (a `requires` entry that is not `introduced`/`presumed_known`) are skipped. `now` is part of
    the signature for symmetry with the other domain functions; ordering does not depend on it.
    """
    del now
    keyed: list[tuple[tuple[int, int, int, float, str], str]] = []
    for item in items:
        view = learner_items.get(item.item_id)
        if view is None:
            continue
        source = queue_source(item.kind, view, max_level, item.level)
        if source is None:
            continue
        ready = all(
            (req := learner_items.get(r)) is not None and req.status in READY_STATUSES
            for r in item.requires
        )
        if not ready:
            continue
        is_lemma = item.kind == "lemma"
        secondary = -(item.frequency_zipf or 0.0) if is_lemma else float(item.order)
        key = (
            SOURCE_PRIORITY.get(source, 9),
            CEFR_LEVELS.index(item.level),
            int(is_lemma),
            secondary,
            item.item_id,
        )
        keyed.append((key, item.item_id))
    keyed.sort()
    return [item_id for _, item_id in keyed]
