"""Candidate queue, weekly budget, opt-in and opt-out."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_now, require_learner
from app.services import queue as service
from app.services.common import SessionError
from app.services.corpus import item_label
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api", tags=["queue"])

QUEUE_PREVIEW = 20


class BudgetOut(BaseModel):
    lemmas_left: int
    grammar_left: int
    backlog: int


class QueueEntry(BaseModel):
    item_id: str
    label: str
    source: Literal["optin", "article", "wordlist"]
    kind: Literal["lemma", "grammar", "construction"]


class QueueOut(BaseModel):
    budget: BudgetOut
    next: list[QueueEntry]


class OptStatusOut(BaseModel):
    item_id: str
    status: str
    candidate_source: str | None


@router.get("/queue", response_model=QueueOut, operation_id="getQueue")
def get_queue(
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> QueueOut:
    budget, backlog = service.weekly_budget(db, learner, now())
    queued = service.candidate_items(db, learner, now())[:QUEUE_PREVIEW]
    return QueueOut(
        budget=BudgetOut(lemmas_left=budget.lemmas, grammar_left=budget.grammar, backlog=backlog),
        next=[
            QueueEntry(
                item_id=item.id,
                label=item_label(item),
                source=source,  # type: ignore[arg-type]
                kind=item.kind,  # type: ignore[arg-type]
            )
            for item, source in queued
        ],
    )


@router.post("/items/{item_id}/optin", response_model=OptStatusOut, operation_id="optinItem")
def optin_item(
    item_id: str,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    now: Callable[[], datetime] = Depends(get_now),
) -> OptStatusOut:
    try:
        row = service.optin(db, learner, item_id, now())
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return OptStatusOut(item_id=item_id, status=row.status, candidate_source=row.candidate_source)


@router.post("/items/{item_id}/optout", response_model=OptStatusOut, operation_id="optoutItem")
def optout_item(
    item_id: str,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
) -> OptStatusOut:
    try:
        row = service.optout(db, learner, item_id)
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return OptStatusOut(item_id=item_id, status=row.status, candidate_source=row.candidate_source)
