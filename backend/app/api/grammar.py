"""Grammar reference endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services import corpus
from app.store.db import get_session
from app.store.models import Item, ItemPrerequisite

router = APIRouter(prefix="/api", tags=["grammar"])


class GrammarSummary(BaseModel):
    id: str
    title_it: str
    level: str


class GrammarExample(BaseModel):
    de: str
    it: str


class GrammarDetail(BaseModel):
    id: str
    title_it: str
    title_en: str
    level: str
    requires: list[str]
    diagnostic_tags: dict[str, list[str]]
    reference_it: str
    examples: list[GrammarExample]


@router.get("/grammar", response_model=list[GrammarSummary], operation_id="listGrammar")
def list_grammar(session: Session = Depends(get_session)) -> list[GrammarSummary]:
    return [
        GrammarSummary(id=i.id, title_it=i.payload["title_it"], level=i.cefr_level)
        for i in corpus.list_grammar(session)
    ]


@router.get("/grammar/{grammar_id}", response_model=GrammarDetail, operation_id="getGrammar")
def get_grammar(grammar_id: str, session: Session = Depends(get_session)) -> GrammarDetail:
    item = session.get(Item, grammar_id)
    if item is None or item.kind != "grammar":
        raise HTTPException(status_code=404, detail="Grammar point not found")
    requires = [
        r
        for r in session.scalars(
            select(ItemPrerequisite.requires_item_id)
            .where(ItemPrerequisite.item_id == grammar_id)
            .order_by(ItemPrerequisite.requires_item_id)
        )
    ]
    p = item.payload
    return GrammarDetail(
        id=item.id,
        title_it=p["title_it"],
        title_en=p["title_en"],
        level=item.cefr_level,
        requires=requires,
        diagnostic_tags=p.get("diagnostic_tags", {}),
        reference_it=p["reference_it"],
        examples=[GrammarExample(**e) for e in p["examples"]],
    )
