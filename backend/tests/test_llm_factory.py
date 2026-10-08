from datetime import UTC, datetime

from pydantic import SecretStr
from sqlalchemy import select

from app.config import Settings
from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.calls import CallRecord, SqlCallRecorder, last_call_id
from app.llm.factory import build_llm_client
from app.llm.fake import FakeLLMClient
from app.store.db import create_session_factory
from app.store.models import LLMCall


def now() -> datetime:
    return datetime(2026, 10, 5, tzinfo=UTC)


def test_provider_selection(migrated_settings: Settings) -> None:
    factory = create_session_factory(migrated_settings)
    assert isinstance(build_llm_client(migrated_settings, factory, now), FakeLLMClient)
    with_key = migrated_settings.model_copy(update={"anthropic_api_key": SecretStr("sk-test")})
    client = build_llm_client(with_key, factory, now)
    assert isinstance(client, AnthropicLLMClient) and client.name == "anthropic"
    forced = with_key.model_copy(update={"llm_provider": "fake"})
    assert build_llm_client(forced, factory, now).name == "fake"


def test_failed_calls_are_stored(migrated_settings: Settings) -> None:
    factory = create_session_factory(migrated_settings)
    recorder = SqlCallRecorder(factory, now)
    call_id = recorder(
        CallRecord(
            task="grade_sentence",
            prompt_version="v1",
            provider="anthropic",
            model="claude-opus-5-5",
            request={"model": "claude-opus-5-5"},
            error="LLMRefusal: refused",
            stop_reason="refusal",
            latency_ms=12,
        )
    )
    assert call_id == last_call_id()
    with factory() as db:
        row = db.scalars(select(LLMCall)).one()
        assert row.error == "LLMRefusal: refused" and row.response is None
        assert row.stop_reason == "refusal" and row.ts.tzinfo is not None


def test_recorder_stores_timing_and_publishes_live_events(migrated_settings: Settings) -> None:
    from app.llm.live import LiveBroadcaster

    live = LiveBroadcaster(enabled=True)
    recorder = SqlCallRecorder(create_session_factory(migrated_settings), now, live)
    record = CallRecord(
        task="gloss",
        prompt_version="v1",
        provider="openrouter",
        model="vendor/m",
        request={"messages": []},
        response={"translation": "casa"},
        latency_ms=1300,
        attempts=2,
        http_statuses=[429, 200],
        retry_wait_ms=1000,
        ttfb_ms=200,
        download_ms=50,
        overhead_ms=50,
        reasoning_tokens=7,
        upstream_provider="Fireworks",
        request_chars=321,
    )
    recorder.started(record)
    call_id = recorder(record)
    with create_session_factory(migrated_settings)() as session:
        row = session.scalars(select(LLMCall)).one()
    assert row.id == call_id and row.attempts == 2 and row.http_statuses == [429, 200]
    assert (row.retry_wait_ms, row.ttfb_ms, row.download_ms, row.overhead_ms) == (1000, 200, 50, 50)
    assert (row.reasoning_tokens, row.upstream_provider, row.request_chars) == (7, "Fireworks", 321)
    started, finished = live.snapshot()
    assert (started.type, finished.type) == ("call_started", "call_finished")
    assert started.data["id"] == finished.data["id"] == record.uid
    assert finished.data["db_id"] == call_id and finished.data["http_statuses"] == [429, 200]
    assert finished.data["started_ts"] == started.data["ts"]


def test_recorder_without_live_or_disabled_publishes_nothing(migrated_settings: Settings) -> None:
    from app.llm.live import LiveBroadcaster

    for live in (None, LiveBroadcaster(enabled=False)):
        recorder = SqlCallRecorder(create_session_factory(migrated_settings), now, live)
        record = CallRecord(
            task="gloss", prompt_version="v1", provider="fake", model="fake", request={}
        )
        recorder.started(record)
        assert recorder(record) is not None
        if live is not None:
            assert live.snapshot() == []
