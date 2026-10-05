"""Learner setup endpoints."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_now, require_learner
from app.api.settings import SettingsOut
from app.services import learner as service
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api", tags=["learner"])

CefrLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2"]


class LearnerOut(BaseModel):
    id: int
    level: CefrLevel
    known_languages: list[str]
    explanation_language: str
    settings: SettingsOut


class LearnerCreate(BaseModel):
    level: CefrLevel
    known_languages: list[str] = Field(default_factory=lambda: ["it"])
    explanation_language: str = "it"


class LearnerUpdate(BaseModel):
    level: CefrLevel | None = None
    known_languages: list[str] | None = None
    explanation_language: str | None = None


def to_out(learner: Learner) -> LearnerOut:
    return LearnerOut(
        id=learner.id,
        level=learner.level,  # type: ignore[arg-type]
        known_languages=list(learner.known_languages or []),
        explanation_language=learner.explanation_language,
        settings=SettingsOut.model_validate(service.settings_of(learner), from_attributes=True),
    )


@router.get("/learner", response_model=LearnerOut, operation_id="getLearner")
def get_learner(learner: Learner = Depends(require_learner)) -> LearnerOut:
    return to_out(learner)


@router.post(
    "/learner",
    response_model=LearnerOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="createLearner",
)
def create_learner(
    body: LearnerCreate,
    session: Session = Depends(get_session),
    now: Callable[[], datetime] = Depends(get_now),
) -> LearnerOut:
    try:
        learner = service.create_learner(
            session, body.level, body.known_languages, body.explanation_language, now()
        )
    except service.LearnerExistsError:
        raise HTTPException(status_code=409, detail="Learner already exists") from None
    return to_out(learner)


@router.put("/learner", response_model=LearnerOut, operation_id="updateLearner")
def update_learner(
    body: LearnerUpdate,
    session: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> LearnerOut:
    updated = service.update_learner(
        session,
        learner,
        now(),
        level=body.level,
        known_languages=body.known_languages,
        explanation_language=body.explanation_language,
    )
    return to_out(updated)
