"""Grammar drills: a mini-session of exercises on one grammar point or construction."""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.domain.drills import plan_drill
from app.services.common import BuiltCard, SessionError
from app.services.learner import settings_of
from app.services.production import (
    KNOWN_STATUSES,
    _grammar_intro_card,
    flat_tags,
    production_card,
)
from app.store.models import Exercise, Item, Learner, LearnerItem

DRILL_PREFIX = "drill-"
DRILL_KINDS = ("grammar", "construction")


def build_drill(
    db: Session, learner: Learner, item_id: str, now: datetime
) -> tuple[str, list[BuiltCard]]:
    """Create a drill session on `item_id`: its intro card when the point is new, then
    `drill_size` pending production exercises (choice, cloze, then open ones).

    The exercises are generated lazily by `prepare`, like those of a review session; answers
    produce the usual events, so a drill counts for the item's memory and mastery.
    """
    item = db.get(Item, item_id)
    if item is None or item.suspended or item.kind not in DRILL_KINDS:
        raise SessionError(404, "Grammar point not found")
    learner_item = db.get(LearnerItem, (learner.id, item.id))
    is_new = learner_item is None or learner_item.status not in KNOWN_STATUSES
    session_id = f"{DRILL_PREFIX}{uuid.uuid4().hex}"

    sequence: list[tuple[Exercise, BuiltCard]] = []
    if is_new:
        sequence.append(_grammar_intro_card(learner.id, session_id, item, now))
    steps = plan_drill(settings_of(learner).drill_size, flat_tags(item))
    for position, step in enumerate(steps, 1):
        exercise = Exercise(
            id=uuid.uuid4().hex,
            learner_id=learner.id,
            type="production",
            prompt={"subtype": step.subtype, "drill_position": f"{position} of {len(steps)}"},
            solution={},
            targets=[
                {
                    "item_id": item.id,
                    "facet": "production",
                    "weight": 1.0,
                    "new": is_new,
                    "role": "primary",
                    "kind": item.kind,
                    "focus_tags": list(step.focus_tags),
                }
            ],
            generator="pending",
            created_at=now,
            session_id=session_id,
            status="pending",
        )
        sequence.append((exercise, production_card(exercise)))
    db.add_all(exercise for exercise, _ in sequence)
    db.commit()
    return session_id, [card for _, card in sequence]


def drill_title(item: Item | None) -> str | None:
    """The history title of a drill on `item`."""
    if item is None:
        return None
    title = item.payload.get("title_it") or item.payload.get("pattern")
    return f"Esercizi: {title}" if title else None
