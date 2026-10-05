"""Build the configured `LLMClient` (design: docs/design/M6-providers.md §2, §3)."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import anthropic
import openai
from google import genai
from google.genai import types as genai_types
from pydantic import SecretStr, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.base import TypedTasks
from app.llm.calls import CallRecorder, SqlCallRecorder
from app.llm.client import LLMClient
from app.llm.config import DEFAULT_MODEL, PROVIDERS, TaskConfig, resolve_tasks
from app.llm.fake import FAKE_MODEL, FakeLLMClient
from app.llm.google_client import GeminiLLMClient
from app.llm.openai_client import OPENROUTER_BASE_URL, OpenAICompatibleLLMClient
from app.llm.types import (
    ExerciseRequest,
    ExplainRequest,
    Explanation,
    GeneratedExercise,
    Gloss,
    GlossRequest,
    GradeRequest,
    GradeResult,
    SimplifiedText,
    SimplifyRequest,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 120.0
KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class LLMConfigError(Exception):
    """The LLM configuration is invalid (unknown provider, missing model or API key)."""


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() or None if value is not None else None


def _keys(settings: Settings) -> dict[str, str | None]:
    return {
        "anthropic": _secret(settings.anthropic_api_key),
        "openai": _secret(settings.openai_api_key),
        "google": _secret(settings.gemini_api_key),
        "openrouter": _secret(settings.openrouter_api_key),
    }


def resolve_routes(settings: Settings) -> dict[str, TaskConfig]:
    """Per task: provider and model resolved as task setting -> global default -> built-in.

    The built-in default exists for Anthropic only; any other provider needs a model.
    """
    try:
        tasks = resolve_tasks(settings.llm_tasks)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise LLMConfigError(
            f"invalid LLMLL_LLM_TASKS ({problems}); providers are {', '.join(PROVIDERS)}"
        ) from exc
    default = settings.llm_provider
    resolved: dict[str, TaskConfig] = {}
    for task, cfg in tasks.items():
        provider = cfg.provider or default
        explicit = (settings.llm_tasks.get(task) or {}).get("model")
        model: str | None = explicit or (settings.llm_model if provider == default else None)
        if provider == "fake":
            model = FAKE_MODEL
        elif provider == "anthropic":
            model = model or DEFAULT_MODEL
        elif not model:
            raise LLMConfigError(
                f"task {task!r} uses provider {provider!r} but no model is configured: set "
                "LLMLL_LLM_MODEL (for the default provider) or a 'model' for the task in "
                "LLMLL_LLM_TASKS"
            )
        _check_model_matches_provider(task, provider, str(model), settings)
        resolved[task] = cfg.model_copy(update={"provider": provider, "model": str(model)})
    return resolved


def _check_model_matches_provider(task: str, provider: str, model: str, settings: Settings) -> None:
    """Catch the common mix-up of an OpenRouter id (`<vendor>/<model>`) used with another
    provider, before any (paid) call fails with an opaque "model not found"."""
    if "/" not in model or provider in ("openrouter", "fake"):
        return
    if provider == "google" and model.startswith("models/") and model.count("/") == 1:
        return  # google-genai accepts the "models/<id>" form
    if provider == "openai" and settings.openai_base_url:
        return  # other OpenAI-compatible servers may use slashes in model ids
    raise LLMConfigError(
        f"task {task!r}: model {model!r} looks like an OpenRouter id (<vendor>/<model>) but the "
        f"provider is {provider!r}. Use provider 'openrouter' for that id, or the provider's own "
        "model id."
    )


def _warn_ignored_settings(settings: Settings, tasks: dict[str, TaskConfig]) -> None:
    for task, overrides in settings.llm_tasks.items():
        cfg = tasks.get(task)
        if cfg and cfg.provider != "anthropic" and "effort" in overrides:
            logger.warning(
                "task %r: 'effort' only applies to Anthropic and is ignored for provider %r",
                task,
                cfg.provider,
            )
    for task, cfg in tasks.items():
        if cfg.provider == "anthropic" and cfg.structured_output != "native":
            raise LLMConfigError(
                f"task {task!r}: structured_output={cfg.structured_output!r} is not supported "
                "for provider 'anthropic' (it always uses native structured output)"
            )


class RoutingLLMClient(TypedTasks):
    """Dispatches every task to the client of its configured provider."""

    def __init__(
        self, name: str, clients: dict[str, LLMClient], tasks: dict[str, TaskConfig]
    ) -> None:
        self.name = name
        self._clients = clients
        self._tasks = tasks

    def _client(self, task: str) -> LLMClient:
        return self._clients[str(self._tasks[task].provider)]

    def generate_exercise(self, req: ExerciseRequest) -> GeneratedExercise:
        return self._client("generate_exercise").generate_exercise(req)

    def grade_sentence(self, req: GradeRequest) -> GradeResult:
        return self._client("grade_sentence").grade_sentence(req)

    def explain(self, req: ExplainRequest) -> Explanation:
        return self._client("explain").explain(req)

    def simplify_text(self, req: SimplifyRequest) -> SimplifiedText:
        return self._client("simplify_text").simplify_text(req)

    def gloss(self, req: GlossRequest) -> Gloss:
        return self._client("gloss").gloss(req)


def build_llm_client(
    settings: Settings, session_factory: sessionmaker[Session], now: Callable[[], datetime]
) -> LLMClient:
    return build_llm_client_with_recorder(settings, SqlCallRecorder(session_factory, now))


def _build_provider(
    provider: str,
    settings: Settings,
    key: str,
    tasks: dict[str, TaskConfig],
    recorder: CallRecorder,
) -> Any:
    if provider == "anthropic":
        sdk = anthropic.Anthropic(
            api_key=key, max_retries=settings.llm_max_retries, timeout=TIMEOUT_SECONDS
        )
        return AnthropicLLMClient(
            sdk, tasks, recorder, refusal_fallback=settings.llm_refusal_fallback
        )
    if provider == "openai":
        sdk = openai.OpenAI(
            api_key=key,
            base_url=settings.openai_base_url or None,
            max_retries=settings.llm_max_retries,
            timeout=TIMEOUT_SECONDS,
        )
        return OpenAICompatibleLLMClient(sdk, "openai", tasks, recorder)
    if provider == "openrouter":
        headers = {}
        if settings.openrouter_app_name:
            headers["X-Title"] = settings.openrouter_app_name
        if settings.openrouter_site_url:
            headers["HTTP-Referer"] = settings.openrouter_site_url
        sdk = openai.OpenAI(
            api_key=key,
            base_url=OPENROUTER_BASE_URL,
            default_headers=headers or None,
            max_retries=settings.llm_max_retries,
            timeout=TIMEOUT_SECONDS,
        )
        return OpenAICompatibleLLMClient(sdk, "openrouter", tasks, recorder)
    if provider == "google":
        sdk = genai.Client(
            api_key=key,
            http_options=genai_types.HttpOptions(
                timeout=int(TIMEOUT_SECONDS * 1000),
                retry_options=genai_types.HttpRetryOptions(attempts=settings.llm_max_retries + 1),
            ),
        )
        return GeminiLLMClient(sdk, tasks, recorder)
    raise LLMConfigError(f"unknown provider {provider!r}")  # pragma: no cover


def build_llm_client_with_recorder(
    settings: Settings, recorder: CallRecorder, *, allow_fake_fallback: bool = True
) -> LLMClient:
    """`allow_fake_fallback=False` (used by eval-grader) turns "no key at all" into an error
    instead of silently simulating the LLM."""
    tasks = resolve_routes(settings)
    _warn_ignored_settings(settings, tasks)
    keys = _keys(settings)
    if allow_fake_fallback and settings.llm_provider != "fake" and not any(keys.values()):
        logger.warning("no LLM API key configured: using the fake LLM")
        return FakeLLMClient(recorder)
    used = sorted({str(cfg.provider) for cfg in tasks.values()})
    for provider in used:
        if provider != "fake" and not keys[provider]:
            raise LLMConfigError(
                f"{KEY_ENV[provider]} is not set but provider {provider!r} is configured for "
                "at least one task"
            )
    clients: dict[str, Any] = {}
    for provider in used:
        if provider == "fake":
            clients[provider] = FakeLLMClient(recorder)
        else:
            clients[provider] = _build_provider(
                provider, settings, str(keys[provider]), tasks, recorder
            )
    if len(clients) == 1:
        return next(iter(clients.values()))
    return RoutingLLMClient(settings.llm_provider, clients, tasks)
