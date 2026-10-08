"""In-process broadcaster of LLM call events for the debug pane
(design: docs/design/M7-observability.md §3.1).

Publishers are worker threads (sync endpoints run in a threadpool); subscribers are async SSE
handlers, each with a bounded `asyncio.Queue` on its own event loop. Events are handed across
with `loop.call_soon_threadsafe`; a slow subscriber loses its oldest events. A ring buffer keeps
the most recent events for new subscribers. Disabled (the default) it stores nothing: the events
contain full prompts and responses.
"""

import asyncio
import itertools
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

BUFFER_SIZE = 200
SUBSCRIBER_QUEUE_SIZE = 100


@dataclass(frozen=True)
class LiveEvent:
    seq: int  # increasing per broadcaster; used as the SSE event id
    type: str  # "call_started" | "call_finished"
    data: dict[str, Any]


class Subscription:
    """A bounded queue owned by one event loop; `offer` must run on that loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, size: int) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=size)

    def offer(self, event: LiveEvent) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # slow client: drop the oldest event
        self.queue.put_nowait(event)


class LiveBroadcaster:
    def __init__(
        self,
        enabled: bool = False,
        buffer_size: int = BUFFER_SIZE,
        queue_size: int = SUBSCRIBER_QUEUE_SIZE,
    ) -> None:
        self.enabled = enabled
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._buffer: deque[LiveEvent] = deque(maxlen=buffer_size)
        self._subscribers: set[Subscription] = set()
        self._seq = itertools.count(1)

    def publish(self, type: str, data: dict[str, Any]) -> None:
        """Thread-safe; never raises. A no-op when disabled."""
        if not self.enabled:
            return
        try:
            with self._lock:
                event = LiveEvent(next(self._seq), type, data)
                self._buffer.append(event)
                subscribers = list(self._subscribers)
            for sub in subscribers:
                try:
                    sub.loop.call_soon_threadsafe(sub.offer, event)
                except RuntimeError:  # its loop is closed
                    self.unsubscribe(sub)
        except Exception:  # pragma: no cover - never break an LLM call
            logger.exception("could not publish a live event")

    def subscribe(self, after: int | None = None) -> tuple[list[LiveEvent], Subscription]:
        """Buffered events (those after sequence number `after`, if given and still valid) and
        a subscription for the following ones, taken atomically: no gap, no duplicate.
        Must be called from the subscriber's event loop."""
        sub = Subscription(asyncio.get_running_loop(), self._queue_size)
        with self._lock:
            backlog = list(self._buffer)
            if after is not None and backlog and after <= backlog[-1].seq:
                backlog = [e for e in backlog if e.seq > after]
            self._subscribers.add(sub)
        return backlog, sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def snapshot(self) -> list[LiveEvent]:
        with self._lock:
            return list(self._buffer)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
