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
