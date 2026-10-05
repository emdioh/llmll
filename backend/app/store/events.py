"""Append-only event log and the `item_memory` projection derived from it."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from fsrs import Card
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.domain.config import ProjectionConfig
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
) -> tuple[LearningEvent, ItemMemory | None]:
    """Insert an event and update the projection (incrementally when it is the newest)."""
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
        return event, replay_key(session, learner_id, item_id, facet, cfg)

    row = session.get(ItemMemory, (learner_id, item_id, facet))
    state, _decision = apply(row_to_state(row, item_id, facet), to_event_data(event), cfg)
    return event, _write_state(session, row, learner_id, item_id, facet, state, cfg)
