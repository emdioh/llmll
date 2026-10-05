"""Learning statistics endpoint."""

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_now, require_learner
from app.services import stats as service
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api", tags=["stats"])


class CalibrationBucketOut(BaseModel):
    bucket_low: float
    bucket_high: float
    n: int
    predicted: float
    observed: float
    recalled: int
    assisted: int
    forgotten: int


class StatsOut(BaseModel):
    reviews_7d: int
    reviews_30d: int
    sessions_7d: int
    readings_7d: int
    new_items_7d: int
    observed_retention: float | None
    target_retention: float
    calibration: list[CalibrationBucketOut]
    backlog: int


@router.get("/stats", response_model=StatsOut, operation_id="getStats")
def get_stats(
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> StatsOut:
    return StatsOut(**service.learner_stats(db, learner, now()))
