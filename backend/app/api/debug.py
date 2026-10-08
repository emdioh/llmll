"""Live LLM debug endpoints (design: docs/design/M7-observability.md §3.1).

They expose full prompts and responses, so they exist only when `LLMLL_DEBUG=true`; otherwise
they answer 404 as if they did not exist.

Event format of `GET /api/debug/llm/events` (Server-Sent Events): each frame is
`id: <seq>\\nevent: <type>\\ndata: <json>\\n\\n`, a comment frame `: ping` is sent every
`HEARTBEAT_S` seconds. See the "Event format" section of the design document for the fields.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.live import LiveBroadcaster, LiveEvent
from app.store.db import get_session
from app.store.models import LLMCall

logger = logging.getLogger(__name__)

HEARTBEAT_S = 15.0


def require_debug(request: Request) -> None:
    if not request.app.state.settings.debug:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix="/api/debug", tags=["debug"], dependencies=[Depends(require_debug)])


class DebugCallOut(BaseModel):
    """One `llm_calls` row, in full."""

    id: int
    ts: datetime
    task: str
    prompt_version: str
    provider: str
    model: str
    request: dict[str, Any]
    response: Any | None
    stop_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: int
    error: str | None
    attempts: int | None
    http_statuses: list[int | None] | None
    retry_wait_ms: int | None
    ttfb_ms: int | None
    download_ms: int | None
    overhead_ms: int | None
    upstream_provider: str | None
    request_chars: int | None


@router.get("/llm/calls", response_model=list[DebugCallOut])
def recent_calls(
    limit: int = Query(50, ge=1, le=500), session: Session = Depends(get_session)
) -> list[LLMCall]:
    """The most recent LLM calls, newest first (history beyond the process lifetime)."""
    return list(session.scalars(select(LLMCall).order_by(LLMCall.id.desc()).limit(limit)))


def format_sse(event: LiveEvent) -> str:
    data = json.dumps(event.data, ensure_ascii=False, default=str)
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"


async def event_stream(
    live: LiveBroadcaster, request: Request | None = None, after: int | None = None
) -> AsyncIterator[str]:
    """Buffered events first, then live ones; `: ping` comments while idle."""
    backlog, sub = live.subscribe(after)
    try:
        for event in backlog:
            yield format_sse(event)
        while True:
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_S)
            except TimeoutError:
                if request is not None and await request.is_disconnected():
                    return
                yield ": ping\n\n"
                continue
            yield format_sse(event)
    finally:
        live.unsubscribe(sub)


@router.get(
    "/llm/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def llm_events(request: Request) -> StreamingResponse:
    """Server-Sent Events: the buffered `call_started` / `call_finished` events, then live ones."""
    live: LiveBroadcaster = request.app.state.live
    last = request.headers.get("last-event-id", "")
    after = int(last) if last.isdigit() else None
    return StreamingResponse(
        event_stream(live, request, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
