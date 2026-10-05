"""Event-sourced memory projection: a pure fold over events."""

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime

from fsrs import Card, Rating

from app.domain.config import ProjectionConfig
from app.domain.grading import GradeDecision, grade
from app.domain.mastery import (
    INITIAL_MASTERY,
    INITIAL_N_EFF,
    update_mastery,
    update_tag_counts,
)
from app.domain.scheduling import card_id_for, new_card, review

REVIEW_KINDS = ("review", "implicit", "lookup")


@dataclass(frozen=True)
class EventData:
    id: int
    ts: datetime
    kind: str
    outcome: str | None = None
    evidence_weight: float = 0.0
    diagnostic_tags: tuple[str, ...] = ()
    presumed_known: bool = False
    confidence: float = 1.0


@dataclass(frozen=True)
class MemoryState:
    card_id: int
    card: Card | None = None
    mastery: float = INITIAL_MASTERY
    n_eff: float = INITIAL_N_EFF
    tag_error_counts: dict[str, float] = field(default_factory=dict)
    last_event_id: int = 0

    @classmethod
    def initial(cls, item_id: str, facet: str) -> "MemoryState":
        return cls(card_id=card_id_for(item_id, facet))


def apply(
    state: MemoryState, event: EventData, cfg: ProjectionConfig
) -> tuple[MemoryState, GradeDecision | None]:
    if event.kind == "introduce":
        card = state.card
        if card is None:
            card = review(
                new_card(state.card_id),
                Rating.Good,
                event.ts,
                cfg.desired_retention,
                cfg.fsrs_parameters,
            )
        return replace(state, card=card, last_event_id=event.id), None

    if event.kind in REVIEW_KINDS and event.outcome is not None:
        decision = grade(
            event.outcome,
            state.mastery,
            state.n_eff,
            event.confidence,
            event.presumed_known,
            state.card is None,
            cfg,
        )
        card = state.card
        if decision.rating is not None:
            card = review(
                card or new_card(state.card_id),
                decision.rating,
                event.ts,
                cfg.desired_retention,
                cfg.fsrs_parameters,
            )
        mastery, n_eff = update_mastery(
            state.mastery, state.n_eff, event.outcome, event.evidence_weight, event.confidence, cfg
        )
        tags = update_tag_counts(state.tag_error_counts, event.outcome, event.diagnostic_tags, cfg)
        new_state = replace(
            state,
            card=card,
            mastery=mastery,
            n_eff=n_eff,
            tag_error_counts=tags,
            last_event_id=event.id,
        )
        return new_state, decision

    return replace(state, last_event_id=event.id), None


def replay(
    events: Iterable[EventData], cfg: ProjectionConfig, item_id: str, facet: str
) -> MemoryState:
    """Fold `apply` over events sorted by (ts, id). Callers pass only non-voided events."""
    state = MemoryState.initial(item_id, facet)
    for event in sorted(events, key=lambda e: (e.ts, e.id)):
        state, _ = apply(state, event, cfg)
    return state
