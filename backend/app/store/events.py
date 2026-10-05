"""Append-only event log and the `item_memory` projection derived from it."""

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from fsrs import Card
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.domain.config import ProjectionConfig
from app.domain.grading import GradeDecision
from app.domain.projection import EventData, MemoryState, apply, replay
from app.store.models import ItemMemory, LearningEvent


def to_event_data(event: LearningEvent) -> EventData:
    return EventData(
        id=event.id,
        ts=event.ts,
        kind=event.kind,
        outcome=event.outcome,
        evidence_weight=event.evidence_weight,
        diagnostic_tags=tuple(event.diagnostic_tags or ()),
        presumed_known=event.presumed_known,
        confidence=event.confidence,
    )


def row_to_state(row: ItemMemory | None, item_id: str, facet: str) -> MemoryState:
    if row is None:
        return MemoryState.initial(item_id, facet)
    return MemoryState(
        card_id=MemoryState.initial(item_id, facet).card_id,
        card=Card.from_dict(row.fsrs_card) if row.fsrs_card else None,
        mastery=row.mastery,
        n_eff=row.n_effective,
        tag_error_counts=dict(row.tag_error_counts or {}),
        last_event_id=row.last_event_id,
    )


def _write_state(
    session: Session,
    row: ItemMemory | None,
    learner_id: int,
    item_id: str,
    facet: str,
    state: MemoryState,
    cfg: ProjectionConfig,
) -> ItemMemory:
    if row is None:
        row = ItemMemory(learner_id=learner_id, item_id=item_id, facet=facet)
        session.add(row)
    card = state.card
    row.fsrs_card = card.to_dict() if card else None
    row.due = card.due if card else None
    row.stability = card.stability if card else None
    row.difficulty = card.difficulty if card else None
    row.mastery = state.mastery
    row.n_effective = state.n_eff
    row.tag_error_counts = dict(state.tag_error_counts)
    row.last_event_id = state.last_event_id
    row.projection_version = cfg.version
    return row


def _key_events(
    session: Session, learner_id: int, item_id: str, facet: str
) -> Sequence[LearningEvent]:
    return session.scalars(
        select(LearningEvent)
        .where(
            LearningEvent.learner_id == learner_id,
            LearningEvent.item_id == item_id,
            LearningEvent.facet == facet,
            LearningEvent.voided_by.is_(None),
        )
        .order_by(LearningEvent.ts, LearningEvent.id)
    ).all()


def replay_key(
    session: Session, learner_id: int, item_id: str, facet: str, cfg: ProjectionConfig
) -> ItemMemory | None:
    """Rebuild one `item_memory` row from its non-voided events."""
    events = _key_events(session, learner_id, item_id, facet)
    row = session.get(ItemMemory, (learner_id, item_id, facet))
    if not events:
        if row is not None:
            session.delete(row)
        return None
    state = replay([to_event_data(e) for e in events], cfg, item_id, facet)
    return _write_state(session, row, learner_id, item_id, facet, state, cfg)


def rebuild_projection(session: Session, learner_id: int, cfg: ProjectionConfig) -> int:
    """Rebuild every `item_memory` row of a learner from the event log; return the row count."""
    session.execute(delete(ItemMemory).where(ItemMemory.learner_id == learner_id))
    events = session.scalars(
        select(LearningEvent)
        .where(LearningEvent.learner_id == learner_id, LearningEvent.voided_by.is_(None))
        .order_by(LearningEvent.ts, LearningEvent.id)
    ).all()
    grouped: dict[tuple[str, str], list[EventData]] = defaultdict(list)
    for event in events:
        grouped[(event.item_id, event.facet)].append(to_event_data(event))
    for (item_id, facet), data in grouped.items():
        state = replay(data, cfg, item_id, facet)
        _write_state(session, None, learner_id, item_id, facet, state, cfg)
    session.flush()
    return len(grouped)


def append_event(
    session: Session, cfg: ProjectionConfig, **kwargs: Any
) -> tuple[LearningEvent, ItemMemory | None]:
    """Insert an event and update the projection (incrementally when it is the newest)."""
    event, row, _decision = append_event_with_decision(session, cfg, **kwargs)
    return event, row


def append_event_with_decision(
    session: Session,
    cfg: ProjectionConfig,
    *,
    learner_id: int,
    item_id: str,
    facet: str,
    ts: datetime,
    kind: str,
    outcome: str | None = None,
    evidence_weight: float = 0.0,
    diagnostic_tags: Sequence[str] = (),
    presumed_known: bool = False,
    confidence: float = 1.0,
    exercise_id: str | None = None,
    attempt_id: int | None = None,
    evaluation_id: int | None = None,
    context: str | None = None,
) -> tuple[LearningEvent, ItemMemory | None, GradeDecision | None]:
    """Like `append_event`, also returning the grading decision (None for backdated events)."""
    event = LearningEvent(
        learner_id=learner_id,
        item_id=item_id,
        facet=facet,
        ts=ts,
        kind=kind,
        outcome=outcome,
        evidence_weight=evidence_weight,
        diagnostic_tags=list(diagnostic_tags),
        presumed_known=presumed_known,
        confidence=confidence,
        exercise_id=exercise_id,
        attempt_id=attempt_id,
        evaluation_id=evaluation_id,
        context=context,
    )
    session.add(event)
    session.flush()

    later = session.scalar(
        select(LearningEvent.id)
        .where(
            LearningEvent.learner_id == learner_id,
            LearningEvent.item_id == item_id,
            LearningEvent.facet == facet,
            LearningEvent.voided_by.is_(None),
            or_(
                LearningEvent.ts > ts,
                and_(LearningEvent.ts == ts, LearningEvent.id > event.id),
            ),
        )
        .limit(1)
    )
    if later is not None:
        return event, replay_key(session, learner_id, item_id, facet, cfg), None

    row = session.get(ItemMemory, (learner_id, item_id, facet))
    state, decision = apply(row_to_state(row, item_id, facet), to_event_data(event), cfg)
    return event, _write_state(session, row, learner_id, item_id, facet, state, cfg), decision


def replace_events(
    session: Session,
    cfg: ProjectionConfig,
    old_events: Sequence[LearningEvent],
    replacement_evaluation_id: int,
    changes: Callable[[LearningEvent], dict[str, Any]],
) -> list[LearningEvent]:
    """Void `old_events` and append a copy of each with its original timestamp.

    `changes(event)` returns the fields that differ in the copy (outcome, confidence, tags...).
    The copies get new ids, in the order of the originals, so replay order (`ts`, `id`) stays
    deterministic. Afterwards every affected (item, facet) is rebuilt from its non-voided events.
    Flushes only; the caller owns the transaction.
    """
    copies: list[LearningEvent] = []
    keys: set[tuple[int, str, str]] = set()
    ordered = sorted(old_events, key=lambda e: e.id)
    for old in ordered:
        fields: dict[str, Any] = {
            "learner_id": old.learner_id,
            "item_id": old.item_id,
            "facet": old.facet,
            "ts": old.ts,
            "kind": old.kind,
            "outcome": old.outcome,
            "evidence_weight": old.evidence_weight,
            "diagnostic_tags": list(old.diagnostic_tags or ()),
            "presumed_known": old.presumed_known,
            "confidence": old.confidence,
            "exercise_id": old.exercise_id,
            "attempt_id": old.attempt_id,
            "context": old.context,
            **changes(old),
            "evaluation_id": replacement_evaluation_id,
        }
        copy = LearningEvent(**fields)
        session.add(copy)
        copies.append(copy)
        keys.add((old.learner_id, old.item_id, old.facet))
    session.flush()
    for old in ordered:
        old.voided_by = replacement_evaluation_id
    session.flush()
    for learner_id, item_id, facet in sorted(keys):
        replay_key(session, learner_id, item_id, facet, cfg)
    return copies
