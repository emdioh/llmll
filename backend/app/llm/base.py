"""Code shared by the provider adapters (design: docs/design/M6-providers.md §3).

`TypedTasks` maps the typed `LLMClient` methods onto one `_run(task, values, output)`.
`ProviderClient` implements `_run` for chat-style providers (OpenAI-compatible, Gemini): prompt
loading, native or `json` structured output with one retry, error mapping and `llm_calls`
logging. Subclasses only build the SDK request and normalize the SDK response.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm import render
from app.llm.calls import CallRecord, CallRecorder
from app.llm.client import LLMError, LLMRefusal
from app.llm.config import TaskConfig, route_label
from app.llm.prompt_loader import load_prompt, render_user
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

T = TypeVar("T", bound=BaseModel)


class TypedTasks:
    """The five typed tasks, all delegating to `_run`."""

    name: str
    _tasks: dict[str, TaskConfig]

    def _run(self, task: str, values: dict[str, str], output: type[T]) -> T:
        raise NotImplementedError

    @property
    def routes(self) -> dict[str, str]:
        """`{task: "provider/model"}`, reported by the health endpoint."""
        return {task: route_label(cfg, self.name) for task, cfg in self._tasks.items()}

    def generate_exercise(self, req: ExerciseRequest) -> GeneratedExercise:
        return self._run("generate_exercise", render.exercise_vars(req), GeneratedExercise)

    def grade_sentence(self, req: GradeRequest) -> GradeResult:
        return self._run("grade_sentence", render.grade_vars(req), GradeResult)

    def explain(self, req: ExplainRequest) -> Explanation:
        return self._run("explain", render.explain_vars(req), Explanation)

    def simplify_text(self, req: SimplifyRequest) -> SimplifiedText:
        return self._run("simplify_text", render.simplify_vars(req), SimplifiedText)

    def gloss(self, req: GlossRequest) -> Gloss:
        return self._run("gloss", render.gloss_vars(req), Gloss)


@dataclass
class Completion:
    """A provider response, normalized."""

    parsed: BaseModel | None = None  # native mode: the SDK's parsed output
    text: str | None = None
    stop_reason: str | None = None
    refusal: str | None = None  # set when the model (or a safety filter) refused
    truncated: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


class InvalidOutput(LLMError):
    """The reply is not valid JSON for the response model (retried once, both modes)."""

    def __init__(self, message: str, text: str | None) -> None:
        super().__init__(message)
        self.text = text


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_MAX_FEEDBACK = 2000


def json_system(system: str, output: type[BaseModel]) -> str:
    schema = json.dumps(output.model_json_schema(), ensure_ascii=False)
    return (
        f"{system}\n\n## Output format\nReply with a single JSON object and nothing else (no "
        "Markdown fences, no commentary). It must validate against this JSON schema:\n"
        f"{schema}"
    )


def loggable(value: Any) -> Any:
    """A JSON-safe copy of a request (response models become their class name)."""
    if isinstance(value, type):
        return value.__name__
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        if value.get("type") == "json_schema" and isinstance(value.get("json_schema"), dict):
            # A response-format schema: log its name, not the whole schema on every call.
            return f"json_schema:{value['json_schema'].get('name')}"
        return {str(k): loggable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [loggable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class ProviderClient(TypedTasks):
    """Template for chat-style providers; see the module docstring."""

    def __init__(self, tasks: dict[str, TaskConfig], recorder: CallRecorder) -> None:
        self._tasks = tasks
        self._record = recorder

    # --- hooks -----------------------------------------------------------------------------

    def _request(
        self,
        cfg: TaskConfig,
        system: str,
        stable: str,
        variable: str,
        output: type[BaseModel],
        native: bool,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _complete(self, request: dict[str, Any], native: bool) -> Completion:
        raise NotImplementedError

    def _classify(self, exc: Exception) -> type[LLMError] | None:
        """`LLMUnavailable` / `LLMError` for the SDK's API errors; None for anything else."""
        raise NotImplementedError

    # --- plumbing --------------------------------------------------------------------------

    def _run(self, task: str, values: dict[str, str], output: type[T]) -> T:
        cfg = self._tasks[task]
        system, user_template, version = load_prompt(task)
        stable, variable = render_user(user_template, values)
        native = cfg.structured_output == "native"
        if not native:
            system = json_system(system, output)
        try:
            return self._attempt(task, version, cfg, system, stable, variable, output, native)
        except InvalidOutput as first:
            logger.warning("%s: invalid JSON from %s, retrying once: %s", task, self.name, first)
            feedback = (
                f"\n\nYour previous reply was rejected: {str(first)[:_MAX_FEEDBACK]}\n"
                f"Previous reply:\n{(first.text or '')[:_MAX_FEEDBACK]}\n"
                "Reply again with only the corrected JSON object."
            )
            return self._attempt(
                task, version, cfg, system, stable, variable + feedback, output, native
            )

    def _attempt(
        self,
        task: str,
        version: str,
        cfg: TaskConfig,
        system: str,
        stable: str,
        variable: str,
        output: type[T],
        native: bool,
    ) -> T:
        request = self._request(cfg, system, stable, variable, output, native)
        record = CallRecord(
            task=task,
            prompt_version=version,
            provider=self.name,
            model=cfg.model,
            request=loggable({**request, "structured_output": cfg.structured_output}),
        )
        started = time.monotonic()
        try:
            completion = self._complete(request, native)
            record.latency_ms = int((time.monotonic() - started) * 1000)
            record.stop_reason = completion.stop_reason
            record.input_tokens = completion.input_tokens
            record.output_tokens = completion.output_tokens
            record.cache_read_tokens = completion.cache_read_tokens
            record.cache_write_tokens = completion.cache_write_tokens
            return self._finish(completion, record, output, native)
        except LLMError as exc:
            record.error = f"{type(exc).__name__}: {exc}"
            raise
        except Exception as exc:
            record.latency_ms = int((time.monotonic() - started) * 1000)
            kind = self._classify(exc)
            record.error = f"{type(exc).__name__}: {exc}"
            if kind is None:
                raise
            raise kind(record.error) from exc
        finally:
            self._record(record)

    @staticmethod
    def _finish(completion: Completion, record: CallRecord, output: type[T], native: bool) -> T:
        if completion.refusal is not None:
            record.response = {"refusal": completion.refusal, "text": completion.text}
            raise LLMRefusal(f"the model refused the request ({completion.refusal})")
        if completion.truncated:
            record.response = completion.text
            raise LLMError(f"the response was truncated (stop_reason={completion.stop_reason})")
        parsed: BaseModel | None = completion.parsed if native else None
        if not isinstance(parsed, output):
            # json mode, or a native reply the SDK did not parse (or that came back wrapped in
            # a code fence): validate the text ourselves; InvalidOutput is retried once.
            record.response = completion.text
            parsed = _parse_json(completion.text, output)
        record.response = parsed.model_dump(mode="json")
        return parsed  # type: ignore[return-value]


def _parse_json(text: str | None, output: type[T]) -> T:
    if not text or not text.strip():
        raise InvalidOutput("invalid structured output: the reply is empty", text)
    body = text.strip()
    if match := _FENCE.match(body):
        body = match.group(1)
    try:
        return output.model_validate_json(body)
    except ValidationError as exc:
        raise InvalidOutput(f"invalid structured output: {exc}", text) from exc
