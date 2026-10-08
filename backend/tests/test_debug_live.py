"""Live LLM debug: the broadcaster, the debug endpoints (404 unless LLMLL_DEBUG) and SSE."""

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import debug as debug_api
from app.config import Settings
from app.llm.live import LiveBroadcaster
from app.main import create_app

from .conftest import Clock, import_fixture

# --- the broadcaster -----------------------------------------------------------------------------


def test_disabled_broadcaster_stores_nothing() -> None:
    live = LiveBroadcaster(enabled=False)
    live.publish("call_started", {"id": "a"})
    assert live.snapshot() == []


def test_buffer_is_bounded_and_sequence_numbers_increase() -> None:
    live = LiveBroadcaster(enabled=True, buffer_size=200)
    for i in range(250):
        live.publish("call_started", {"n": i})
    events = live.snapshot()
    assert len(events) == 200
    assert [e.seq for e in events] == list(range(51, 251)) and events[0].data == {"n": 50}


def test_subscribe_returns_backlog_and_live_events_without_gap_or_duplicate() -> None:
    live = LiveBroadcaster(enabled=True)
    live.publish("call_started", {"n": 1})
    live.publish("call_started", {"n": 2})

    async def scenario() -> list[int]:
        backlog, sub = live.subscribe()
        assert [e.data["n"] for e in backlog] == [1, 2]
        await asyncio.get_running_loop().run_in_executor(
            None, live.publish, "call_finished", {"n": 3}
        )
        event = await asyncio.wait_for(sub.queue.get(), timeout=2)
        live.unsubscribe(sub)
        assert live.subscriber_count == 0
        return [event.data["n"]]

    assert asyncio.run(scenario()) == [3]


def test_resume_after_a_sequence_number() -> None:
    live = LiveBroadcaster(enabled=True)
    for i in range(5):
        live.publish("call_started", {"n": i})

    async def scenario() -> tuple[list[int], list[int]]:
        resumed, sub1 = live.subscribe(after=3)
        stale, sub2 = live.subscribe(after=999)  # e.g. the server restarted: send everything
        live.unsubscribe(sub1)
        live.unsubscribe(sub2)
        return [e.seq for e in resumed], [e.seq for e in stale]

    assert asyncio.run(scenario()) == ([4, 5], [1, 2, 3, 4, 5])


def test_slow_subscriber_loses_its_oldest_events() -> None:
    live = LiveBroadcaster(enabled=True, queue_size=3)

    async def scenario() -> list[int]:
        _, sub = live.subscribe()
        for i in range(10):
            live.publish("call_started", {"n": i})
        await asyncio.sleep(0.1)  # let the queued hand-overs run
        received = []
        while not sub.queue.empty():
            received.append(sub.queue.get_nowait().data["n"])
        live.unsubscribe(sub)
        return received

    assert asyncio.run(scenario()) == [7, 8, 9]


def test_concurrent_publishers_from_threads() -> None:
    live = LiveBroadcaster(enabled=True, queue_size=1000)

    async def scenario() -> list[int]:
        _, sub = live.subscribe()
        threads = [
            threading.Thread(target=lambda: [live.publish("call_started", {}) for _ in range(100)])
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        received = []
        while len(received) < 400:
            received.append((await asyncio.wait_for(sub.queue.get(), timeout=5)).seq)
        for t in threads:
            t.join()
        live.unsubscribe(sub)
        return received

    received = asyncio.run(scenario())
    assert sorted(received) == list(range(1, 401))


def test_a_closed_loop_does_not_break_publishing() -> None:
    live = LiveBroadcaster(enabled=True)

    async def subscribe_and_leave() -> None:
        live.subscribe()

    asyncio.run(subscribe_and_leave())  # its loop is closed afterwards, nobody unsubscribed
    live.publish("call_started", {"n": 1})
    assert live.subscriber_count == 0 and len(live.snapshot()) == 1


# --- endpoints -----------------------------------------------------------------------------------


@pytest.fixture
def debug_settings(migrated_settings: Settings) -> Settings:
    return migrated_settings.model_copy(update={"debug": True})


@pytest.fixture
def debug_client(debug_settings: Settings, clock: Clock) -> Iterator[TestClient]:
    import_fixture(debug_settings, when=clock.now)
    with TestClient(create_app(debug_settings, now=clock)) as client:
        yield client


def explain(client: TestClient) -> None:
    assert client.post("/api/learner", json={"level": "A2"}).status_code == 201
    response = client.post("/api/grammar/gram:cases/explain", json={"question": "Perché?"})
    assert response.status_code == 200, response.text


def test_debug_endpoints_are_404_unless_enabled(curriculum_client: TestClient) -> None:
    assert curriculum_client.get("/api/health").json()["debug"] is False
    assert curriculum_client.get("/api/debug/llm/calls").status_code == 404
    assert curriculum_client.get("/api/debug/llm/events").status_code == 404
    explain(curriculum_client)
    assert curriculum_client.app.state.live.snapshot() == []  # type: ignore[attr-defined]


def test_health_reports_the_debug_flag(debug_client: TestClient) -> None:
    assert debug_client.get("/api/health").json()["debug"] is True


def test_fake_llm_calls_are_published_and_listed(debug_client: TestClient) -> None:
    explain(debug_client)
    events = debug_client.app.state.live.snapshot()  # type: ignore[attr-defined]
    assert [e.type for e in events] == ["call_started", "call_finished"]
    started, finished = events[0].data, events[1].data
    assert started["id"] == finished["id"] and started["task"] == "explain"
    assert started["provider"] == "fake" and started["request"] and started["ts"]
    assert finished["db_id"] and finished["response"]["markdown"] and finished["error"] is None
    assert finished["latency_ms"] >= 0 and "ttfb_ms" in finished and "reasoning_tokens" in finished
    calls = debug_client.get("/api/debug/llm/calls?limit=5").json()
    assert len(calls) == 1 and calls[0]["id"] == finished["db_id"] and calls[0]["task"] == "explain"
    assert calls[0]["request"] and calls[0]["response"]["markdown"]
    assert debug_client.get("/api/debug/llm/calls?limit=0").status_code == 422


def test_debug_endpoints_need_the_access_token(debug_settings: Settings, clock: Clock) -> None:
    secured = debug_settings.model_copy(update={"access_token": SecretStr("s3cret")})
    with TestClient(create_app(secured, now=clock)) as client:
        assert client.get("/api/debug/llm/calls").status_code == 401
        ok = client.get("/api/debug/llm/calls", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


# --- Server-Sent Events over a real socket -------------------------------------------------------


@contextmanager
def serve(app: Any) -> Iterator[str]:
    """Run the app on a free port in a thread (TestClient cannot stream an open response)."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", timeout_graceful_shutdown=2
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the test server did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def read_frames(response: httpx.Response, count: int, deadline_s: float = 8.0) -> list[dict]:
    """The first `count` frames (events and comments) of an SSE response, never hanging."""
    frames: list[dict] = []
    current: dict[str, str] = {}
    deadline = time.monotonic() + deadline_s
    for line in response.iter_lines():
        if line == "":
            if current:
                frames.append(current)
                current = {}
            if len(frames) >= count:
                break
        elif line.startswith(":"):
            frames.append({"comment": line[1:].strip()})
            if len(frames) >= count:
                break
        else:
            name, _, value = line.partition(": ")
            current[name] = value
        if time.monotonic() > deadline:
            break
    return frames


def wait_for(condition: Any, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "condition not reached"
        time.sleep(0.02)


def test_sse_streams_buffered_then_live_events_from_another_thread(
    debug_settings: Settings,
) -> None:
    app = create_app(debug_settings)
    live: LiveBroadcaster = app.state.live
    live.publish("call_started", {"id": "old", "task": "gloss"})
    live.publish("call_finished", {"id": "old", "task": "gloss", "latency_ms": 5})

    def publish_later() -> None:
        wait_for(lambda: live.subscriber_count == 1)  # the client is connected
        live.publish("call_started", {"id": "new", "task": "explain"})
        live.publish("call_finished", {"id": "new", "task": "explain", "latency_ms": 7})

    with serve(app) as base:
        publisher = threading.Thread(target=publish_later)
        publisher.start()
        with httpx.stream(
            "GET", f"{base}/api/debug/llm/events", timeout=httpx.Timeout(10.0)
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = read_frames(response, 4)
        publisher.join(timeout=10)
        assert [(f["event"], json.loads(f["data"])["id"]) for f in frames] == [
            ("call_started", "old"),
            ("call_finished", "old"),
            ("call_started", "new"),
            ("call_finished", "new"),
        ]
        assert [f["id"] for f in frames] == ["1", "2", "3", "4"]
        assert json.loads(frames[3]["data"])["latency_ms"] == 7
        wait_for(lambda: live.subscriber_count == 0)  # disconnect is detected and cleaned up


def test_sse_resumes_after_last_event_id(debug_settings: Settings) -> None:
    app = create_app(debug_settings)
    for i in range(3):
        app.state.live.publish("call_started", {"id": str(i)})
    with serve(app) as base:
        with httpx.stream(
            "GET",
            f"{base}/api/debug/llm/events",
            headers={"Last-Event-ID": "2"},
            timeout=httpx.Timeout(10.0),
        ) as response:
            frames = read_frames(response, 1)
    assert [json.loads(f["data"])["id"] for f in frames] == ["2"]


def test_sse_heartbeat_comment(debug_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(debug_api, "HEARTBEAT_S", 0.2)
    app = create_app(debug_settings)
    with serve(app) as base:
        with httpx.stream(
            "GET", f"{base}/api/debug/llm/events", timeout=httpx.Timeout(10.0)
        ) as response:
            frames = read_frames(response, 2)
    assert frames == [{"comment": "ping"}, {"comment": "ping"}]


def test_sse_is_404_when_disabled(migrated_settings: Settings) -> None:
    with serve(create_app(migrated_settings)) as base:
        assert httpx.get(f"{base}/api/debug/llm/events", timeout=5).status_code == 404
