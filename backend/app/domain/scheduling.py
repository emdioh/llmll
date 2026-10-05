"""FSRS scheduling wrapper. Deterministic: no fuzzing, no learning steps."""

import zlib
from datetime import datetime
from functools import lru_cache

from fsrs import Card, Rating, Scheduler

Parameters = tuple[float, ...] | None


@lru_cache
def get_scheduler(desired_retention: float, parameters: Parameters = None) -> Scheduler:
    # No learning steps: items are introduced inside exercises, not drilled in-session.
    # No fuzzing: projections must be reproducible.
    extra = {"parameters": parameters} if parameters else {}
    return Scheduler(
        **extra,
        desired_retention=desired_retention,
        learning_steps=(),
        relearning_steps=(),
        enable_fuzzing=False,
    )


def card_id_for(item_id: str, facet: str) -> int:
    return zlib.crc32(f"{item_id}|{facet}".encode())


def new_card(card_id: int) -> Card:
    return Card(card_id=card_id)


def review(
    card: Card,
    rating: Rating,
    now: datetime,
    desired_retention: float,
    parameters: Parameters = None,
) -> Card:
    new, _log = get_scheduler(desired_retention, parameters).review_card(card, rating, now)
    return new


def retrievability(
    card: Card | None, now: datetime, desired_retention: float, parameters: Parameters = None
) -> float:
    if card is None or card.last_review is None:
        return 0.0
    return get_scheduler(desired_retention, parameters).get_card_retrievability(card, now)
