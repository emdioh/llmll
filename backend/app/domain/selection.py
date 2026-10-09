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
    due.sort(key=lambda m: _priority(m, now, desired_retention, parameters))
    return due[:limit]


def select_ahead(
    memories: Iterable[MemoryView],
    now: datetime,
    limit: int,
    desired_retention: float = 0.85,
    parameters: tuple[float, ...] | None = None,
) -> list[MemoryView]:
    """Not-yet-due memories to practise ahead of schedule, in the same order as `select_due`.

    Used to fill a session up to its cap when fewer memories are due (e.g. a second session on
    the same day): the ones closest to being forgotten come first.
    """
    ahead = [m for m in memories if m.due is not None and m.due > now]
    ahead.sort(key=lambda m: _priority(m, now, desired_retention, parameters))
    return ahead[:limit]


def _priority(
    m: MemoryView, now: datetime, desired_retention: float, parameters: tuple[float, ...] | None
) -> tuple[float, float, str, int]:
    return (
        retrievability(m.card, now, desired_retention, parameters),
        -(m.frequency_zipf or 0.0),
        m.item_id,
        FACET_ORDER.get(m.facet, 9),
    )
