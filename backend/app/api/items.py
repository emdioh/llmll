"""Corpus browsing endpoints."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.services import corpus
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api", tags=["items"])

Kind = Literal["lemma", "grammar", "construction"]
Status = Literal["unseen", "presumed_known", "candidate", "introduced", "suspended"]


class MemoryEntry(BaseModel):
    facet: str
    due: datetime | None
    mastery: float
    stability: float | None


class ItemSummary(BaseModel):
    id: str
    kind: Kind
    level: str
    label: str
    translation_it: str | None
    status: Status | None
    memory: list[MemoryEntry]


class ItemList(BaseModel):
    total: int
    items: list[ItemSummary]


class ItemDetail(BaseModel):
    id: str
    kind: Kind
    level: str
    label: str
    translation_it: str | None
    payload: dict[str, Any]
    interference: dict[str, Any]
    requires: list[str]
    frequency_zipf: float | None
    suspended: bool
    status: Status | None
    candidate_source: str | None
    introduced_at: datetime | None
    memory: list[MemoryEntry]


def _learner_id(session: Session) -> int | None:
    learner = session.get(Learner, 1)
    return learner.id if learner else None


@router.get("/items", response_model=ItemList, operation_id="listItems")
def list_items(
    kind: Kind | None = None,
    level: str | None = None,
    status: Status | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> Any:
    flt = corpus.ItemFilter(kind=kind, level=level, status=status, q=q)
    total, items = corpus.list_items(session, _learner_id(session), flt, limit, offset)
    return {"total": total, "items": items}


@router.get("/items/{item_id}", response_model=ItemDetail, operation_id="getItem")
def get_item(item_id: str, session: Session = Depends(get_session)) -> Any:
    item = corpus.get_item(session, _learner_id(session), item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item
