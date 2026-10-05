"""Choosing which memories to review."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from fsrs import Card

from app.domain.scheduling import retrievability

# On equal retrievability the easier facet is reviewed first.
FACET_ORDER = {"recognition": 0, "production": 1}


@dataclass(frozen=True)
class MemoryView:
    item_id: str
    facet: str
    due: datetime | None
    card: Card | None
    frequency_zipf: float | None = None


def select_due(
    memories: Iterable[MemoryView],
    now: datetime,
    limit: int,
    desired_retention: float = 0.85,
    parameters: tuple[float, ...] | None = None,
) -> list[MemoryView]:
    """Due memories ordered by retrievability (asc), frequency (desc), then item id."""
    due = [m for m in memories if m.due is not None and m.due <= now]
    due.sort(
        key=lambda m: (
            retrievability(m.card, now, desired_retention, parameters),
            -(m.frequency_zipf or 0.0),
            m.item_id,
            FACET_ORDER.get(m.facet, 9),
        )
    )
    return due[:limit]
