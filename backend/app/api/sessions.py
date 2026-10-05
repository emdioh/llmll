"""Flashcard session endpoints."""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_now, require_learner
from app.services import sessions as service
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api", tags=["sessions"])

CardType = Literal["flashcard_intro", "flashcard_recognition", "flashcard_production"]


class CardExample(BaseModel):
    de: str
    it: str


class CardPrompt(BaseModel):
    """Prompt shown to the learner. Fields depend on the card type; unused ones are null."""

    de: str | None = None
    it: str | None = None
    pos: str | None = None
    options: list[str] | None = None
    needs_article: bool | None = None
    lemma: str | None = None
    article: str | None = None
    plural: str | None = None
    translation_it: str | None = None
    translation_en: str | None = None
    example: CardExample | None = None
    interference_note: str | None = None


class SessionCard(BaseModel):
    exercise_id: str
    type: CardType
    item_id: str
    prompt: CardPrompt
    hint: str | None = None


class SessionOut(BaseModel):
    session_id: str
    cards: list[SessionCard]


class AnswerPayload(BaseModel):
    choice: int | None = None
    text: str | None = None


class AnswerIn(BaseModel):
    exercise_id: str
    answer: AnswerPayload = Field(default_factory=AnswerPayload)
    used_hint: bool = False
    duration_ms: int | None = Field(default=None, ge=0)


class ExpectedAnswer(BaseModel):
    text: str
    lemma: str
    article: str | None = None
    plural: str | None = None
    translation_it: str
    example: CardExample | None = None


class MemoryOut(BaseModel):
    facet: str
    due: datetime | None
    mastery: float


class AnswerOut(BaseModel):
    outcome: Literal["correct", "assisted", "error"]
    expected: ExpectedAnswer
    diagnostic_tags: list[str]
    feedback_it: str
    memory: MemoryOut | None


@router.post(
    "/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSession",
)
def create_session(
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> SessionOut:
    session_id, cards = service.build_session(db, learner, now())
    return SessionOut(
        session_id=session_id,
        cards=[
            SessionCard(
                exercise_id=c.exercise_id,
                type=c.type,  # type: ignore[arg-type]
                item_id=c.item_id,
                prompt=CardPrompt.model_validate(c.prompt),
                hint=c.hint,
            )
            for c in cards
        ],
    )


@router.post(
    "/sessions/{session_id}/answers", response_model=AnswerOut, operation_id="submitAnswer"
)
def submit_answer(
    session_id: str,
    body: AnswerIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> Any:
    try:
        return service.submit_answer(
            db,
            learner,
            session_id,
            body.exercise_id,
            body.answer.model_dump(exclude_none=True),
            body.used_hint,
            body.duration_ms,
            now(),
        )
    except service.SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
