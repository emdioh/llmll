"""Logging of every LLM call into the `llm_calls` table."""

import logging
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.store.models import LLMCall

logger = logging.getLogger(__name__)

_last_call_id: ContextVar[int | None] = ContextVar("llm_last_call_id", default=None)


def last_call_id() -> int | None:
    """Id of the `llm_calls` row written by the most recent call in this execution context."""
    return _last_call_id.get()


@dataclass
class CallRecord:
    task: str
    prompt_version: str
    provider: str
    model: str
    request: dict[str, Any]
    response: Any = None
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    latency_ms: int = 0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


CallRecorder = Callable[[CallRecord], int | None]


class SqlCallRecorder:
    """Writes call records in their own transaction, so failures are logged too."""

    def __init__(self, session_factory: sessionmaker[Session], now: Callable[[], datetime]) -> None:
        self._factory = session_factory
        self._now = now

    def __call__(self, record: CallRecord) -> int | None:
        try:
            with self._factory() as session:
                row = LLMCall(
                    ts=self._now(),
                    task=record.task,
                    prompt_version=record.prompt_version,
                    provider=record.provider,
                    model=record.model,
                    request=record.request,
                    response=record.response,
                    stop_reason=record.stop_reason,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cache_read_tokens=record.cache_read_tokens,
                    cache_write_tokens=record.cache_write_tokens,
                    latency_ms=record.latency_ms,
                    error=record.error,
                )
                session.add(row)
                session.commit()
                _last_call_id.set(row.id)
                return row.id
        except Exception:
            logger.exception("could not log LLM call %s", record.task)
            _last_call_id.set(None)
            return None
