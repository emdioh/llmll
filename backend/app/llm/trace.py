"""Timing of the HTTP exchanges behind one LLM call (design: docs/design/M7-observability.md §1.1).

The SDK clients are built with request/response event hooks (`tracing_hooks`). The hooks append
to the `CallTrace` held in a `ContextVar`, which the adapter sets around the SDK call
(`trace_call`). The sync SDK clients run hooks in the calling thread, so the context variable is
the right scope. `derive_timing` is pure. Measuring must never change behaviour: the hooks
swallow every exception.
"""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HttpAttempt:
    """One HTTP request. Times are `time.monotonic()` seconds."""

    request_at: float
    headers_at: float | None = None  # response headers received (None: transport failure)
    status: int | None = None


@dataclass
class CallTrace:
    attempts: list[HttpAttempt] = field(default_factory=list)
    ended: float = 0.0  # when the SDK call returned or raised


@dataclass(frozen=True)
class Timing:
    """The breakdown of one call; see `derive_timing`. Durations are in milliseconds."""

    attempts: int
    http_statuses: list[int | None]
    retry_wait_ms: int
    ttfb_ms: int | None
    download_ms: int | None
    overhead_ms: int | None


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def derive_timing(attempts: list[HttpAttempt], ended: float, latency_ms: int) -> Timing | None:
    """Break `latency_ms` into retry wait, time to first byte, download and overhead.

    - `retry_wait_ms`: first request -> start of the final attempt (failed attempts + back-off);
    - `ttfb_ms`: final attempt, request sent -> response headers (connection set-up included);
    - `download_ms`: response headers of the final attempt -> the SDK call returned;
    - `overhead_ms`: the rest, floored at 0.

    Returns None when no HTTP request was traced (fake or mocked clients). When the final
    attempt got no response (transport failure) `ttfb_ms`, `download_ms` and `overhead_ms` are
    None: the time cannot be attributed.
    """
    if not attempts:
        return None
    first, last = attempts[0], attempts[-1]
    retry_wait = _ms(last.request_at - first.request_at)
    statuses = [a.status for a in attempts]
    if last.headers_at is None:
        return Timing(len(attempts), statuses, retry_wait, None, None, None)
    ttfb = _ms(last.headers_at - last.request_at)
    download = _ms(ended - last.headers_at)
    overhead = max(0, latency_ms - (retry_wait + ttfb + download))
    return Timing(len(attempts), statuses, retry_wait, ttfb, download, overhead)


_current: ContextVar[CallTrace | None] = ContextVar("llm_call_trace", default=None)


@contextmanager
def trace_call() -> Iterator[CallTrace]:
    """Trace the HTTP attempts made inside the block (sets `trace.ended` on exit)."""
    trace = CallTrace()
    token = _current.set(trace)
    try:
        yield trace
    finally:
        trace.ended = time.monotonic()
        _current.reset(token)


def _on_request(request: Any) -> None:
    try:
        trace = _current.get()
        if trace is not None:
            trace.attempts.append(HttpAttempt(request_at=time.monotonic()))
    except Exception:  # pragma: no cover - measuring must never break a call
        logger.debug("trace request hook failed", exc_info=True)


def _on_response(response: Any) -> None:
    try:
        trace = _current.get()
        if trace is None:
            return
        pending = next((a for a in reversed(trace.attempts) if a.headers_at is None), None)
        if pending is not None:
            pending.headers_at = time.monotonic()
            pending.status = int(response.status_code)
    except Exception:  # pragma: no cover
        logger.debug("trace response hook failed", exc_info=True)


def tracing_hooks() -> dict[str, list[Callable[[Any], None]]]:
    """`event_hooks` for an `httpx` / `httpx2` client (both use the same shape)."""
    return {"request": [_on_request], "response": [_on_response]}
