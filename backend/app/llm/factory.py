"""Build the configured `LLMClient`."""

import logging
from collections.abc import Callable
from datetime import datetime

import anthropic
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.calls import SqlCallRecorder
from app.llm.client import LLMClient
from app.llm.config import resolve_tasks
from app.llm.fake import FakeLLMClient

logger = logging.getLogger(__name__)


def build_llm_client(
    settings: Settings, session_factory: sessionmaker[Session], now: Callable[[], datetime]
) -> LLMClient:
    recorder = SqlCallRecorder(session_factory, now)
    key = settings.anthropic_api_key
    if settings.llm_provider == "fake" or key is None or not key.get_secret_value():
        if settings.llm_provider != "fake":
            logger.warning("no ANTHROPIC_API_KEY configured: using the fake LLM")
        return FakeLLMClient(recorder)
    sdk = anthropic.Anthropic(
        api_key=key.get_secret_value(), max_retries=settings.llm_max_retries, timeout=120.0
    )
    return AnthropicLLMClient(
        sdk,
        resolve_tasks(settings.llm_tasks),
        recorder,
        refusal_fallback=settings.llm_refusal_fallback,
    )
