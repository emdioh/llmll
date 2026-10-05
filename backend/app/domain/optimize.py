"""Review logs for FSRS parameter fitting (design: docs/design/M5.md §4). Pure."""

from collections.abc import Mapping, Sequence

from fsrs import Rating, ReviewLog

from app.domain.config import ProjectionConfig
from app.domain.projection import EventData, MemoryState, apply
from app.domain.scheduling import card_id_for


def build_review_logs(
    events_by_card: Mapping[tuple[str, str], Sequence[EventData]], cfg: ProjectionConfig
) -> list[ReviewLog]:
    """One `ReviewLog` per scheduler-visible review, per `(item_id, facet)` card.

    The events are folded with the projection itself, so a log carries exactly the rating the
    scheduler saw: `introduce` is the card's first review (Good), reviews carry the rating of
    the grading decision, and events that did not move the card (low confidence) are skipped.
    Callers pass non-voided events only. Logs are ordered by time, then card.
    """
    logs: list[ReviewLog] = []
    for (item_id, facet), events in sorted(events_by_card.items()):
        state = MemoryState.initial(item_id, facet)
        card_id = card_id_for(item_id, facet)
        for event in sorted(events, key=lambda e: (e.ts, e.id)):
            rating: Rating | None = None
            if event.kind == "introduce":
                if state.card is None:
                    rating = Rating.Good
                new_state, _ = apply(state, event, cfg)
            else:
                new_state, decision = apply(state, event, cfg)
                if decision is not None:
                    rating = decision.rating
            state = new_state
            if rating is not None:
                logs.append(ReviewLog(card_id, rating, event.ts, None))
    logs.sort(key=lambda log: (log.review_datetime, log.card_id))
    return logs
