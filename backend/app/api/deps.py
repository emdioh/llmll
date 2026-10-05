"""Shared FastAPI dependencies."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.llm.client import LLMClient
from app.nlp.languagetool import LanguageToolClient
from app.services.learner import LEARNER_ID
from app.store.db import get_session
from app.store.models import Learner


def get_now(request: Request) -> Callable[[], datetime]:
    return getattr(request.app.state, "now", None) or (lambda: datetime.now(UTC))


def require_learner(session: Session = Depends(get_session)) -> Learner:
    learner = session.get(Learner, LEARNER_ID)
    if learner is None:
        raise HTTPException(status_code=404, detail="No learner set up yet")
    return learner


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


def get_languagetool(request: Request) -> LanguageToolClient | None:
    return request.app.state.languagetool
