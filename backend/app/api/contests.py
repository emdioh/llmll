"""Contest endpoints ("Secondo me era giusto")."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_now, require_learner
from app.services import contests as service
from app.services.common import SessionError
from app.store.db import get_session
from app.store.models import Contest, Learner
from app.store.models import Evaluation as EvaluationRow

router = APIRouter(prefix="/api", tags=["contests"])


class ContestIn(BaseModel):
    item_ids: list[str] = Field(default_factory=list)
    """Contested items; empty means the whole evaluation."""
    reason: str | None = Field(default=None, max_length=2000)


class ContestOut(BaseModel):
    id: int
    evaluation_id: int
    item_ids: list[str]
    reason: str | None
    status: Literal["open", "resolved"]
    verdict: Literal["accepted", "rejected", "partial"] | None
    resolver: str
    rationale: str
    replacement_evaluation_id: int | None
    created_at: datetime
    resolved_at: datetime | None


class ContestItemOutcome(BaseModel):
    item_id: str
    label: str
    previous: Literal["correct", "assisted", "error"] | None
    outcome: Literal["correct", "assisted", "error"]


class ContestResultOut(BaseModel):
    contest: ContestOut
    evaluation_id: int
    """The evaluation that is current after the contest (the replacement when one was made)."""
    items: list[ContestItemOutcome]


def _contest_out(row: Contest) -> ContestOut:
    return ContestOut.model_validate(row, from_attributes=True)


def get_resolver(request: Request) -> service.ContestResolver:
    return request.app.state.contest_resolver


def _result(outcome: service.ContestOutcome) -> ContestResultOut:
    return ContestResultOut(
        contest=_contest_out(outcome.contest),
        evaluation_id=outcome.evaluation_id,
        items=[ContestItemOutcome(**i) for i in outcome.items],
    )


@router.post(
    "/evaluations/{evaluation_id}/contest",
    response_model=ContestResultOut,
    operation_id="contestEvaluation",
)
def contest_evaluation(
    evaluation_id: int,
    body: ContestIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    resolver: service.ContestResolver = Depends(get_resolver),
    now: Callable[[], datetime] = Depends(get_now),
) -> ContestResultOut:
    try:
        row = db.get(EvaluationRow, evaluation_id)
        if row is None:
            raise SessionError(404, "Evaluation not found")
        return _result(
            service.contest_evaluation(
                db, learner, row, body.item_ids, body.reason, resolver, now()
            )
        )
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.post(
    "/attempts/{attempt_id}/contest",
    response_model=ContestResultOut,
    operation_id="contestAttempt",
)
def contest_attempt(
    attempt_id: int,
    body: ContestIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    resolver: service.ContestResolver = Depends(get_resolver),
    now: Callable[[], datetime] = Depends(get_now),
) -> ContestResultOut:
    """Contest an attempt; flashcard attempts from before M4 get their evaluation on demand."""
    try:
        row = service.ensure_flashcard_evaluation(db, learner, attempt_id, now())
        return _result(
            service.contest_evaluation(
                db, learner, row, body.item_ids, body.reason, resolver, now()
            )
        )
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.get("/contests", response_model=list[ContestOut], operation_id="listContests")
def list_contests(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
) -> list[ContestOut]:
    rows = db.scalars(select(Contest).order_by(Contest.id.desc()).limit(limit)).all()
    return [_contest_out(r) for r in rows]
