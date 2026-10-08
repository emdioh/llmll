"""`LLMClient` for OpenAI and OpenAI-compatible APIs such as OpenRouter
(design: docs/design/M6-providers.md §3)."""

from typing import Any

import openai
from openai.lib._parsing._completions import type_to_response_format_param
from pydantic import BaseModel

from app.llm.base import Completion, ProviderClient
from app.llm.calls import CallRecorder
from app.llm.client import LLMError, LLMUnavailable
from app.llm.config import TaskConfig

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_TOKEN_LIMIT_PARAMS = ("max_tokens", "max_completion_tokens")


def _upstream_provider(completion: Any) -> str | None:
    """OpenRouter names the upstream provider that served the call in a top-level `provider`
    field, which the SDK keeps as an extra attribute."""
    value = getattr(completion, "provider", None)
    if value is None:
        extra = getattr(completion, "model_extra", None)
        value = extra.get("provider") if isinstance(extra, dict) else None
    return value if isinstance(value, str) and value else None


class OpenAICompatibleLLMClient(ProviderClient):
    """`client` is an `openai.OpenAI`; `provider_name` is `openai` or `openrouter`."""

    def __init__(
        self,
        client: Any,
        provider_name: str,
        tasks: dict[str, TaskConfig],
        recorder: CallRecorder,
    ) -> None:
        super().__init__(tasks, recorder)
        self._client = client
        self.name = provider_name
        # OpenAI's newer models reject `max_tokens`; OpenRouter documents `max_tokens`.
        self._limit_param = (
            "max_tokens" if provider_name == "openrouter" else "max_completion_tokens"
        )

    def _request(
        self,
        cfg: TaskConfig,
        system: str,
        stable: str,
        variable: str,
        output: type[BaseModel],
        native: bool,
    ) -> dict[str, Any]:
        user = "\n\n".join(part for part in (stable, variable) if part)
        request: dict[str, Any] = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if not any(key in cfg.params for key in _TOKEN_LIMIT_PARAMS):
            request[self._limit_param] = cfg.max_tokens
        request.update(cfg.params)
        # Native mode sends the strict JSON schema (built by the SDK's own converter) but the
        # reply is parsed by our tolerant parser, not the SDK's `parse()`: some models (e.g. on
        # OpenRouter) wrap the JSON in a ```json fence, which `parse()` rejects with an error
        # that loses the reply text.
        request["response_format"] = (
            type_to_response_format_param(output) if native else {"type": "json_object"}
        )
        return request

    def _complete(self, request: dict[str, Any], native: bool) -> Completion:
        return self._normalize(self._client.chat.completions.create(**request))

    @staticmethod
    def _normalize(completion: Any) -> Completion:
        usage = getattr(completion, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None)
        out_details = getattr(usage, "completion_tokens_details", None)
        result = Completion(
            reasoning_tokens=getattr(out_details, "reasoning_tokens", None),
            upstream_provider=_upstream_provider(completion),
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            cache_read_tokens=getattr(details, "cached_tokens", None),
            cache_write_tokens=getattr(details, "cache_write_tokens", None),
        )
        choices = getattr(completion, "choices", None)
        if not choices:
            return result
        choice = choices[0]
        message = choice.message
        finish = getattr(choice, "finish_reason", None)
        result.stop_reason = finish
        result.text = getattr(message, "content", None)
        result.parsed = getattr(message, "parsed", None)
        if getattr(message, "refusal", None):
            result.refusal = str(message.refusal)
        elif finish == "content_filter":
            result.refusal = "content_filter"
        elif finish == "length":
            result.truncated = True
        return result

    def _classify(self, exc: Exception) -> type[LLMError] | None:
        if isinstance(
            exc, (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError)
        ):
            return LLMUnavailable
        if isinstance(exc, (openai.APIStatusError, openai.APIError)):
            return LLMError
        return None
