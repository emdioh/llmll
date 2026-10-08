"""Logging of every LLM call into the `llm_calls` table."""

import dataclasses
import logging
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.llm.live import LiveBroadcaster
from app.llm.trace import CallTrace, derive_timing
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
    # Timing breakdown and token detail (docs/design/M7-observability.md §1).
    attempts: int | None = None
    http_statuses: list[int | None] | None = None
    retry_wait_ms: int | None = None
    ttfb_ms: int | None = None
    download_ms: int | None = None
    overhead_ms: int | None = None
    reasoning_tokens: int | None = None
    upstream_provider: str | None = None
    request_chars: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Identifies the call in the live debug stream before it has a database id.
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: datetime | None = None


CallRecorder = Callable[[CallRecord], int | None]


def record_to_dict(record: CallRecord) -> dict[str, Any]:
    """Every field of a record, for the offline trace (`eval-grader --trace`)."""
    data = dataclasses.asdict(record)
    for key in ("uid", "extra"):
        data.pop(key)
    if record.started_at is not None:
        data["started_at"] = record.started_at.isoformat()
    return data


def stamp_timing(record: CallRecord, trace: CallTrace | None, started: float) -> None:
    """Set `latency_ms` and the HTTP timing breakdown once the SDK call has returned or raised."""
    ended = trace.ended if trace is not None and trace.ended else time.monotonic()
    record.latency_ms = int((ended - started) * 1000)
    if trace is None:
        return
    try:
        timing = derive_timing(trace.attempts, ended, record.latency_ms)
    except Exception:  # pragma: no cover - measuring must never break a call
        logger.exception("could not derive the timing of %s", record.task)
        return
    if timing is not None:
        record.attempts = timing.attempts
        record.http_statuses = timing.http_statuses
        record.retry_wait_ms = timing.retry_wait_ms
        record.ttfb_ms = timing.ttfb_ms
        record.download_ms = timing.download_ms
        record.overhead_ms = timing.overhead_ms


def announce_start(recorder: object, record: CallRecord) -> None:
    """Tell the recorder (when it listens) that a call begins; never raises."""
    started = getattr(recorder, "started", None)
    if started is None:
        return
    try:
        started(record)
    except Exception:
        logger.exception("could not announce LLM call %s", record.task)


class SqlCallRecorder:
    """Writes call records in their own transaction, so failures are logged too."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        now: Callable[[], datetime],
        live: LiveBroadcaster | None = None,
    ) -> None:
        self._factory = session_factory
        self._now = now
        self._live = live

    def started(self, record: CallRecord) -> None:
        """Publish `call_started` to the live debug stream (no-op unless it is enabled)."""
        record.started_at = self._now()
        if self._live is not None and self._live.enabled:
            self._live.publish(
                "call_started",
                {
                    "id": record.uid,
                    "ts": record.started_at.isoformat(),
                    "task": record.task,
                    "provider": record.provider,
                    "model": record.model,
                    "prompt_version": record.prompt_version,
                    "request": record.request,
                    "request_chars": record.request_chars,
                },
            )

    def _publish_finished(self, record: CallRecord, ts: datetime, db_id: int | None) -> None:
        if self._live is None or not self._live.enabled:
            return
        data = record_to_dict(record)
        data.pop("started_at", None)
        self._live.publish(
            "call_finished",
            {
                "id": record.uid,
                "db_id": db_id,
                "ts": ts.isoformat(),
                "started_ts": record.started_at.isoformat() if record.started_at else None,
                **data,
            },
        )

    def __call__(self, record: CallRecord) -> int | None:
        ts = self._now()
        db_id: int | None = None
        try:
            db_id = self._write(record, ts)
        finally:
            try:
                self._publish_finished(record, ts, db_id)
            except Exception:
                logger.exception("could not publish LLM call %s", record.task)
        return db_id

    def _write(self, record: CallRecord, ts: datetime) -> int | None:
        try:
            with self._factory() as session:
                row = LLMCall(
                    ts=ts,
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
                    attempts=record.attempts,
                    http_statuses=record.http_statuses,
                    retry_wait_ms=record.retry_wait_ms,
                    ttfb_ms=record.ttfb_ms,
                    download_ms=record.download_ms,
                    overhead_ms=record.overhead_ms,
                    reasoning_tokens=record.reasoning_tokens,
                    upstream_provider=record.upstream_provider,
                    request_chars=record.request_chars,
                )
                session.add(row)
                session.commit()
                _last_call_id.set(row.id)
                return row.id
        except Exception:
            logger.exception("could not log LLM call %s", record.task)
            _last_call_id.set(None)
            return None
