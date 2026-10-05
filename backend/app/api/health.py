"""Health check endpoint."""

from importlib.metadata import version

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.store.db import get_session

router = APIRouter(prefix="/api")


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str


@router.get("/health", response_model=HealthResponse)
def health(response: Response, session: Session = Depends(get_session)) -> HealthResponse:
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
    )
