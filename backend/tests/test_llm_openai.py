"""OpenAI-compatible adapter (OpenAI, OpenRouter) with a mocked SDK client."""

from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest

from app.llm.calls import CallRecord
from app.llm.client import LLMError, LLMRefusal, LLMUnavailable
from app.llm.config import resolve_tasks
from app.llm.openai_client import OpenAICompatibleLLMClient
from app.llm.types import GeneratedExercise, GradeResult

from .llm_helpers import exercise_request, explain_request, grade_request

GENERATED = GeneratedExercise(
    instructions="Traduci.",
    prompt="Il tavolo.",
    glossary=[],
    reference_solutions=["Der Tisch."],
    targets=[],
)


def completion(
    parsed: Any = GENERATED,
    finish: str = "stop",
    refusal: str | None = None,
    content: str | None = None,
) -> SimpleNamespace:
    """A `chat.completions.create` result: `parsed` is serialized into the message content
    (as a model would reply); pass `content` to send any other text."""
    if content is None:
        content = parsed.model_dump_json() if parsed is not None else "raw text"
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=7,
            prompt_tokens_details=SimpleNamespace(cached_tokens=5),
        ),
    )


class FakeCompletions:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.parse_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []

    def _next(self) -> Any:
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result

    def parse(self, **kwargs: Any) -> Any:
        self.parse_calls.append(kwargs)
        return self._next()

    def create(self, **kwargs: Any) -> Any:
        self.create_calls.append(kwargs)
        return self._next()


def make_client(
    *results: Any,
    provider: str = "openai",
    overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[OpenAICompatibleLLMClient, FakeCompletions, list[CallRecord]]:
    completions = FakeCompletions(list(results))
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    records: list[CallRecord] = []

    def record(rec: CallRecord) -> int:
        records.append(rec)
        return len(records)

    base = {t: {"provider": provider, "model": "m-1"} for t in resolve_tasks({})}
    for task, fields in (overrides or {}).items():
        base[task] = {**base[task], **fields}
    client = OpenAICompatibleLLMClient(sdk, provider, resolve_tasks(base), record)
    return client, completions, records


def status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


def test_request_shape_native() -> None:
    client, sdk, _ = make_client(completion())
    assert client.generate_exercise(exercise_request()) == GENERATED
    assert sdk.parse_calls == []  # the SDK's strict parse() is never used
    (call,) = sdk.create_calls
    assert call["model"] == "m-1"
    assert call["max_completion_tokens"] == 4000 and "max_tokens" not in call
    fmt = call["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "GeneratedExercise"
    system, user = call["messages"]
    assert system["role"] == "system" and "German" in system["content"]
    assert user["role"] == "user" and isinstance(user["content"], str)
    # Stable block first, variable block last.
    stable_at = user["content"].index("gram:cases")
    variable_at = user["content"].index("Exercise type: translation")
    assert stable_at < variable_at
    for forbidden in ("output_config", "thinking", "reasoning_effort", "temperature"):
        assert forbidden not in call


def test_openrouter_uses_max_tokens() -> None:
    client, sdk, records = make_client(completion(), provider="openrouter")
    client.generate_exercise(exercise_request())
    (call,) = sdk.create_calls
    assert call["max_tokens"] == 4000 and "max_completion_tokens" not in call
    assert records[0].provider == "openrouter"


def test_params_pass_through_and_effort_is_ignored() -> None:
    overrides = {
        "grade_sentence": {"params": {"reasoning_effort": "high", "max_completion_tokens": 123}}
    }
    client, sdk, _ = make_client(
        completion(GradeResult(overall="correct", corrected_sentence="x", feedback="y")),
        overrides=overrides,
    )
    client.grade_sentence(grade_request())
    (call,) = sdk.create_calls
    assert call["reasoning_effort"] == "high"
    assert call["max_completion_tokens"] == 123  # the explicit parameter wins
    assert "effort" not in call and "output_config" not in call


def test_every_call_is_logged_with_provider_and_usage() -> None:
    client, _, records = make_client(completion())
    client.generate_exercise(exercise_request())
    (rec,) = records
    assert rec.provider == "openai" and rec.model == "m-1"
    assert rec.task == "generate_exercise" and rec.prompt_version == "v1"
    assert rec.stop_reason == "stop"
    assert (rec.input_tokens, rec.output_tokens, rec.cache_read_tokens) == (11, 7, 5)
    assert rec.response["prompt"] == "Il tavolo." and rec.error is None
    assert rec.request["response_format"] == "json_schema:GeneratedExercise"
    assert rec.request["messages"][0]["role"] == "system"


def test_refusal_raises_and_is_logged() -> None:
    client, _, records = make_client(completion(None, refusal="I cannot help"))
    with pytest.raises(LLMRefusal):
        client.explain(explain_request())
    assert records[0].response["refusal"] == "I cannot help" and records[0].error


def test_content_filter_is_a_refusal() -> None:
    client, _, _ = make_client(completion(None, finish="content_filter"))
    with pytest.raises(LLMRefusal):
        client.explain(explain_request())


def test_truncation_is_an_error() -> None:
    client, _, records = make_client(completion(None, finish="length"))
    with pytest.raises(LLMError) as info:
        client.explain(explain_request())
    assert not isinstance(info.value, (LLMRefusal, LLMUnavailable))
    assert records[0].error and records[0].response == "raw text"


def test_native_reply_in_a_code_fence_is_accepted() -> None:
    """Regression: a model on OpenRouter wrapped its JSON in ```json ... ``` and the SDK's
    parse() crashed the whole eval run with a pydantic ValidationError."""
    graded = GradeResult(overall="correct", corrected_sentence="x", feedback="y")
    client, sdk, records = make_client(
        completion(None, content=f"```json\n{graded.model_dump_json(indent=2)}\n```")
    )
    assert client.grade_sentence(grade_request()) == graded
    assert len(sdk.create_calls) == 1 and records[0].error is None


def test_invalid_native_reply_is_retried_once_then_an_error() -> None:
    client, sdk, records = make_client(completion(None, content="Sorry, I can't produce JSON."))
    with pytest.raises(LLMError, match="invalid structured output"):
        client.explain(explain_request())
    assert len(sdk.create_calls) == 2 and len(records) == 2


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(openai.RateLimitError, 429), LLMUnavailable),
        (status_error(openai.InternalServerError, 503), LLMUnavailable),
        (
            openai.APIConnectionError(request=httpx2.Request("POST", "https://x")),
            LLMUnavailable,
        ),
        (status_error(openai.BadRequestError, 400), LLMError),
        (status_error(openai.AuthenticationError, 401), LLMError),
    ],
)
def test_api_errors_are_mapped_and_logged(error: Exception, expected: type[LLMError]) -> None:
    client, _, records = make_client(error)
    with pytest.raises(expected) as info:
        client.explain(explain_request())
    assert type(info.value) is expected
    assert len(records) == 1 and records[0].error and records[0].provider == "openai"


# --- json structured-output mode -----------------------------------------------------------

JSON_OVERRIDES = {"generate_exercise": {"structured_output": "json"}}
VALID_JSON = GENERATED.model_dump_json()


def test_json_mode_valid_reply() -> None:
    client, sdk, records = make_client(
        completion(None, content=f"```json\n{VALID_JSON}\n```"), overrides=JSON_OVERRIDES
    )
    assert client.generate_exercise(exercise_request()) == GENERATED
    assert sdk.parse_calls == []
    (call,) = sdk.create_calls
    assert call["response_format"] == {"type": "json_object"}
    assert "JSON schema" in call["messages"][0]["content"]
    assert "instructions" in call["messages"][0]["content"]
    assert len(records) == 1 and records[0].error is None
    assert records[0].request["structured_output"] == "json"


def test_json_mode_retries_once_with_the_validation_error() -> None:
    client, sdk, records = make_client(
        completion(None, content='{"instructions": 1}'),
        completion(None, content=VALID_JSON),
        overrides=JSON_OVERRIDES,
    )
    assert client.generate_exercise(exercise_request()) == GENERATED
    first, second = sdk.create_calls
    assert "rejected" not in first["messages"][1]["content"]
    retry = second["messages"][1]["content"]
    assert "rejected" in retry and '{"instructions": 1}' in retry and "validation error" in retry
    assert [r.error is None for r in records] == [False, True]  # both attempts are logged


def test_json_mode_invalid_twice_is_an_error() -> None:
    client, sdk, records = make_client(
        completion(None, content="not json"), overrides=JSON_OVERRIDES
    )
    with pytest.raises(LLMError, match="invalid structured output"):
        client.generate_exercise(exercise_request())
    assert len(sdk.create_calls) == 2 and len(records) == 2
    assert all(r.error for r in records)


def test_json_mode_truncation_is_not_retried() -> None:
    client, sdk, _ = make_client(
        completion(None, finish="length", content='{"instr'), overrides=JSON_OVERRIDES
    )
    with pytest.raises(LLMError, match="truncated"):
        client.generate_exercise(exercise_request())
    assert len(sdk.create_calls) == 1
