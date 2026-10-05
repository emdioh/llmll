"""`LLMClient` backed by the Anthropic API (design: docs/design/M2.md §1.2)."""

import logging
import time
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.llm import render
from app.llm.calls import CallRecord, CallRecorder
from app.llm.client import LLMError, LLMRefusal, LLMUnavailable
from app.llm.config import TaskConfig
from app.llm.prompt_loader import load_prompt, render_user
from app.llm.types import (
    ExerciseRequest,
    ExplainRequest,
    Explanation,
    GeneratedExercise,
    GradeRequest,
    GradeResult,
)

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
T = TypeVar("T", bound=BaseModel)


def _usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None),
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", None),
    }


def _raw_text(response: Any) -> str | None:
    texts = [getattr(b, "text", None) for b in getattr(response, "content", None) or []]
    joined = "".join(t for t in texts if isinstance(t, str))
    return joined or None


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


class AnthropicLLMClient:
    name = "anthropic"

    def __init__(
        self,
        client: Any,
        tasks: dict[str, TaskConfig],
        recorder: CallRecorder,
        refusal_fallback: bool = True,
    ) -> None:
        self._client = client
        self._tasks = tasks
        self._record = recorder
        self._refusal_fallback = refusal_fallback

    # --- typed tasks -----------------------------------------------------------------------

    def generate_exercise(self, req: ExerciseRequest) -> GeneratedExercise:
        return self._run("generate_exercise", render.exercise_vars(req), GeneratedExercise)

    def grade_sentence(self, req: GradeRequest) -> GradeResult:
        return self._run("grade_sentence", render.grade_vars(req), GradeResult)

    def explain(self, req: ExplainRequest) -> Explanation:
        return self._run("explain", render.explain_vars(req), Explanation)

    # --- plumbing --------------------------------------------------------------------------

    def _build_request(self, task: str, values: dict[str, str]) -> tuple[dict[str, Any], str]:
        cfg = self._tasks[task]
        system, user_template, version = load_prompt(task)
        stable, variable = render_user(user_template, values)
        content: list[dict[str, Any]] = []
        if stable:
            content.append({"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}})
        content.append({"type": "text", "text": variable})
        request = {
            "model": cfg.model,
            "max_tokens": cfg.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": cfg.effort},
        }
        return request, version

    def _run(self, task: str, values: dict[str, str], output: type[T]) -> T:
        request, version = self._build_request(task, values)
        record = CallRecord(
            task=task,
            prompt_version=version,
            model=request["model"],
            request={
                **request,
                "output_format": output.__name__,
                "betas": [FALLBACK_BETA] if self._refusal_fallback else [],
                "fallbacks": "default" if self._refusal_fallback else None,
            },
        )
        started = time.monotonic()
        try:
            if self._refusal_fallback:
                response = self._client.beta.messages.parse(
                    **request, output_format=output, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self._client.messages.parse(**request, output_format=output)
            record.latency_ms = int((time.monotonic() - started) * 1000)
            record.stop_reason = getattr(response, "stop_reason", None)
            for key, value in _usage(response).items():
                setattr(record, key, value)
            return self._parse(response, record, output)
        except LLMError as exc:
            record.error = f"{type(exc).__name__}: {exc}"
            raise
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
            raise self._api_failure(record, started, LLMUnavailable, exc) from exc
        except anthropic.InternalServerError as exc:
            raise self._api_failure(record, started, LLMUnavailable, exc) from exc
        except anthropic.APIStatusError as exc:
            raise self._api_failure(record, started, LLMError, exc) from exc
        except (ValidationError, ValueError) as exc:
            record.error = f"invalid structured output: {exc}"
            record.latency_ms = int((time.monotonic() - started) * 1000)
            raise LLMError(record.error) from exc
        finally:
            self._record(record)

    @staticmethod
    def _api_failure(
        record: CallRecord, started: float, kind: type[LLMError], exc: Exception
    ) -> LLMError:
        record.latency_ms = int((time.monotonic() - started) * 1000)
        record.error = f"{type(exc).__name__}: {exc}"
        return kind(record.error)

    @staticmethod
    def _parse(response: Any, record: CallRecord, output: type[T]) -> T:
        stop_reason = record.stop_reason
        if stop_reason == "refusal":
            details = _dump(getattr(response, "stop_details", None))
            record.response = {"stop_details": details, "text": _raw_text(response)}
            raise LLMRefusal(f"the model refused the request (stop_details={details})")
        if stop_reason == "max_tokens":
            record.response = _raw_text(response)
            raise LLMError("the response was truncated (stop_reason=max_tokens)")
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            record.response = _raw_text(response)
            raise LLMError("the model returned no structured output")
        record.response = parsed.model_dump(mode="json")
        return parsed
