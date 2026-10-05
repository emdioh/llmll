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
