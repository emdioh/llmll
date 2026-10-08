"""`LLMClient` backed by Google Gemini through `google-genai`
(design: docs/design/M6-providers.md §3)."""

from typing import Any

import httpx
from google.genai import errors, types
from pydantic import BaseModel

from app.llm.base import Completion, ProviderClient
from app.llm.calls import CallRecorder
from app.llm.client import LLMError, LLMUnavailable
from app.llm.config import TaskConfig

# Finish reasons where Gemini withheld the answer for safety or policy reasons.
REFUSAL_FINISH = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
    }
)


def _name(value: Any) -> str | None:
    """Enum member or plain string -> its name."""
    if value is None:
        return None
    return str(getattr(value, "name", value))


class GeminiLLMClient(ProviderClient):
    name = "google"

    def __init__(self, client: Any, tasks: dict[str, TaskConfig], recorder: CallRecorder) -> None:
        super().__init__(tasks, recorder)
        self._client = client

    def _request(
        self,
        cfg: TaskConfig,
        system: str,
        stable: str,
        variable: str,
        output: type[BaseModel],
        native: bool,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "max_output_tokens": cfg.max_tokens,
        }
        if native:
            config["response_schema"] = output
        config.update(cfg.params)
        return {
            "model": cfg.model,
            "contents": [part for part in (stable, variable) if part],
            "config": config,
        }

    def _complete(self, request: dict[str, Any], native: bool) -> Completion:
        response = self._client.models.generate_content(
            model=request["model"],
            contents=request["contents"],
            config=types.GenerateContentConfig(**request["config"]),
        )
        usage = getattr(response, "usage_metadata", None)
        result = Completion(
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            cache_read_tokens=getattr(usage, "cached_content_token_count", None),
            reasoning_tokens=getattr(usage, "thoughts_token_count", None),
        )
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            reason = _name(feedback.block_reason)
            message = getattr(feedback, "block_reason_message", None)
            result.stop_reason = "blocked"
            result.refusal = f"prompt blocked: {reason}" + (f" ({message})" if message else "")
            return result
        candidates = getattr(response, "candidates", None)
        if not candidates:
            return result
        candidate = candidates[0]
        finish = _name(getattr(candidate, "finish_reason", None))
        result.stop_reason = finish
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        texts = [p.text for p in parts if isinstance(getattr(p, "text", None), str)]
        result.text = "".join(texts) or None
        parsed = getattr(response, "parsed", None)
        result.parsed = parsed if isinstance(parsed, BaseModel) else None
        if finish in REFUSAL_FINISH:
            result.refusal = f"finish_reason={finish}"
        elif finish == "MAX_TOKENS":
            result.truncated = True
        return result

    def _classify(self, exc: Exception) -> type[LLMError] | None:
        if isinstance(exc, errors.APIError):
            code = exc.code if isinstance(exc.code, int) else 0
            return LLMUnavailable if code == 429 or code >= 500 else LLMError
        if isinstance(exc, httpx.TransportError):
            return LLMUnavailable
        return None
