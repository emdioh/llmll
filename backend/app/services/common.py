"""Types shared by the session services."""

from dataclasses import dataclass, field
from typing import Any


class SessionError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class BuiltCard:
    exercise_id: str
    type: str
    item_id: str | None
    prompt: Any  # dict for flashcards and grammar intros, str for production exercises
    hint: str | None = None
    status: str = "ready"
    subtype: str | None = None
    instructions: str | None = None
    glossary: list[dict[str, str]] = field(default_factory=list)
    item_ids: list[str] = field(default_factory=list)


PLACEMENT_PREFIX = "placement-"


def event_context(exercise: Any) -> str | None:
    """`learning_events.context` for events of this exercise (`placement` or null)."""
    return "placement" if exercise.session_id.startswith(PLACEMENT_PREFIX) else None


def mark_introduced(learner_item: Any, now: Any, context: str | None) -> None:
    """Set `introduced_at` for items met in normal sessions. Items first met in the placement
    keep it null so that the assessment does not use up the weekly budget of new items."""
    if context is None and learner_item.introduced_at is None:
        learner_item.introduced_at = now
