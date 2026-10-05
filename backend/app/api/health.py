"""Health check endpoint."""

from importlib.metadata import version
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth import auth_mode
from app.store.db import get_session

router = APIRouter(prefix="/api")


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    llm: Literal["anthropic", "openai", "google", "openrouter", "fake"]
    llm_tasks: dict[str, str] = Field(default_factory=dict)
    auth: Literal["enabled", "disabled"]


@router.get("/health", response_model=HealthResponse)
def health(
    request: Request, response: Response, session: Session = Depends(get_session)
) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "error"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if database == "ok" else "error",
        version=version("llmll-backend"),
        database=database,
        llm=request.app.state.llm.name,
        llm_tasks=dict(getattr(request.app.state.llm, "routes", {})),
        auth=auth_mode(request),
    )
