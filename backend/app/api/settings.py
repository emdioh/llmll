"""Learner settings endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_learner
from app.services import learner as service
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsOut(BaseModel):
    weekly_new_lemmas: int
    weekly_new_grammar: int
    desired_retention: float
    review_cap: int
    new_per_session: int
    production_slots: int


class SettingsUpdate(BaseModel):
    weekly_new_lemmas: int | None = Field(default=None, ge=0, le=500)
    weekly_new_grammar: int | None = Field(default=None, ge=0, le=50)
    desired_retention: float | None = Field(default=None, ge=0.7, le=0.97)
    review_cap: int | None = Field(default=None, ge=1, le=500)
    new_per_session: int | None = Field(default=None, ge=0, le=20)
    production_slots: int | None = Field(default=None, ge=0, le=5)


@router.get("/settings", response_model=SettingsOut, operation_id="getSettings")
def get_settings(learner: Learner = Depends(require_learner)) -> SettingsOut:
    return SettingsOut.model_validate(service.settings_of(learner), from_attributes=True)


@router.put("/settings", response_model=SettingsOut, operation_id="updateSettings")
def update_settings(
    body: SettingsUpdate,
    session: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
) -> SettingsOut:
    changes = body.model_dump(exclude_none=True)
    return SettingsOut.model_validate(
        service.update_settings(session, learner, changes), from_attributes=True
    )
