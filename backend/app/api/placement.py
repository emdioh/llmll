"""Initial assessment endpoints."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_now, require_learner
from app.api.sessions import SessionCard, to_card
from app.services import placement as service
from app.services.common import SessionError
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api", tags=["placement"])


class PlacementOut(BaseModel):
    placement_id: str
    cards: list[SessionCard]


class BandResult(BaseModel):
    n: int
    score: float


class PlacementFinishOut(BaseModel):
    placement_id: str
    estimated_level: str
    previous_level: str
    changed: bool
    answered: int
    bands: dict[str, BandResult]


@router.post(
    "/placement",
    response_model=PlacementOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="startPlacement",
)
def start_placement(
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> PlacementOut:
    placement_id, cards = service.start_placement(db, learner, now())
    return PlacementOut(placement_id=placement_id, cards=[to_card(c) for c in cards])


@router.post(
    "/placement/{placement_id}/finish",
    response_model=PlacementFinishOut,
    operation_id="finishPlacement",
)
def finish_placement(
    placement_id: str,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> Any:
    try:
        return service.finish_placement(db, learner, placement_id, now())
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
