"""Explanation endpoints."""

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_llm, get_now, require_learner
from app.llm.client import LLMClient
from app.services import explanations as service
from app.services.common import SessionError
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api", tags=["explanations"])


class ExplanationExampleOut(BaseModel):
    de: str
    translation: str


class ExplanationOut(BaseModel):
    markdown: str
    examples: list[ExplanationExampleOut]
    cached: bool = False


class EvaluationExplainIn(BaseModel):
    item_id: str


class GrammarExplainIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@router.post(
    "/evaluations/{evaluation_id}/explain",
    response_model=ExplanationOut,
    operation_id="explainEvaluation",
)
def explain_evaluation(
    evaluation_id: int,
    body: EvaluationExplainIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    llm: LLMClient = Depends(get_llm),
    now: Callable[[], datetime] = Depends(get_now),
) -> dict:
    try:
        return service.explain_evaluation(db, learner, evaluation_id, body.item_id, llm, now())
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.post(
    "/grammar/{grammar_id}/explain",
    response_model=ExplanationOut,
    operation_id="explainGrammar",
)
def explain_grammar(
    grammar_id: str,
    body: GrammarExplainIn,
    db: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
    llm: LLMClient = Depends(get_llm),
) -> dict:
    try:
        return service.explain_grammar(db, learner, grammar_id, body.question, llm)
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
