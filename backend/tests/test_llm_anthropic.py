from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.llm.anthropic_client import FALLBACK_BETA, AnthropicLLMClient
from app.llm.calls import CallRecord
from app.llm.client import LLMError, LLMRefusal, LLMUnavailable
from app.llm.config import DEFAULT_MODEL, TaskConfig, resolve_tasks
from app.llm.prompt_loader import VARIABLE_MARKER, latest_version, load_prompt, render_user
from app.llm.types import GeneratedExercise, GradeResult

from .llm_helpers import exercise_request, explain_request, grade_request

GENERATED = GeneratedExercise(
    instructions="Traduci.",
    prompt="Il tavolo.",
    glossary=[],
    reference_solutions=["Der Tisch."],
    targets=[],
)
GRADED = GradeResult(overall="correct", corrected_sentence="Der Tisch.", feedback="Bene!")


def usage() -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=11, output_tokens=7, cache_read_input_tokens=5, cache_creation_input_tokens=3
    )


def response(
    parsed: Any = GENERATED, stop_reason: str = "end_turn", **extra: Any
) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=parsed,
        usage=usage(),
        content=[SimpleNamespace(text="raw text")],
        stop_details=None,
        **extra,
    )


class FakeMessages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeSDK:
    def __init__(self, result: Any) -> None:
        self.messages = FakeMessages(result)
        self.beta = SimpleNamespace(messages=FakeMessages(result))


def make_client(
    result: Any, fallback: bool = True
) -> tuple[AnthropicLLMClient, FakeSDK, list[CallRecord]]:
    sdk = FakeSDK(result)
    records: list[CallRecord] = []

    def record(rec: CallRecord) -> int:
        records.append(rec)
        return len(records)

    client = AnthropicLLMClient(sdk, resolve_tasks({}), record, refusal_fallback=fallback)
    return client, sdk, records


def http_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


# --- prompts ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task", ["generate_exercise", "grade_sentence", "explain", "simplify_text", "gloss"]
)
def test_prompt_files_load(task: str) -> None:
    system, user, version = load_prompt(task)
    assert version == f"v{latest_version(task)}" == ("v2" if task == "generate_exercise" else "v1")
    assert system and len(system) > 500
    assert VARIABLE_MARKER in user.template


def test_prompt_content_requirements() -> None:
    grade = load_prompt("grade_sentence").system
    for phrase in ("correct variant", "Stylistic preferences", "concrete", "item_id", "untrusted"):
        assert phrase.lower() in grade.lower() or phrase in grade
    assert "learner_answer" in load_prompt("grade_sentence").user.template
    assert "reference" in load_prompt("explain").system.lower()
    assert "known vocabulary" in load_prompt("generate_exercise").system.lower()


def test_render_user_splits_stable_and_variable() -> None:
    _, user, _ = load_prompt("grade_sentence")
    from app.llm import render

    stable, variable = render_user(user, render.grade_vars(grade_request("a < b")))
    assert "gram:cases" in stable and "curated reference text" in stable
    assert "<learner_answer>a ‹ b</learner_answer>" in variable
    assert "gram:cases" in variable or "Reference solutions" in variable


# --- request building -------------------------------------------------------------------------


def test_request_shape_with_fallback() -> None:
    client, sdk, records = make_client(response())
    result = client.generate_exercise(exercise_request())
    assert result == GENERATED
    assert sdk.messages.calls == []  # beta namespace is used for the fallback
    (call,) = sdk.beta.messages.calls
    assert call["model"] == DEFAULT_MODEL == "claude-opus-5-5"
    assert call["max_tokens"] == 4000
    assert call["output_config"] == {"effort": "medium"}
    assert call["output_format"] is GeneratedExercise
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    for forbidden in ("thinking", "temperature", "top_p"):
        assert forbidden not in call
    system = call["system"]
    assert len(system) == 1 and system[0]["cache_control"] == {"type": "ephemeral"}
    (message,) = call["messages"]
    assert message["role"] == "user"  # no assistant prefill
    stable, variable = message["content"]
    assert stable["cache_control"] == {"type": "ephemeral"}
    assert "reference" in stable["text"] and "gram:cases" in stable["text"]
    assert "cache_control" not in variable
    assert "Exercise type: translation" in variable["text"]
    assert "x-api-key" not in str(records[0].request).lower()


def test_request_without_fallback_uses_plain_namespace() -> None:
    client, sdk, _ = make_client(response(GRADED), fallback=False)
    client.grade_sentence(grade_request())
    assert sdk.beta.messages.calls == []
    (call,) = sdk.messages.calls
    assert "betas" not in call and "fallbacks" not in call
    assert call["output_config"] == {"effort": "high"}
    assert call["output_format"] is GradeResult


def test_task_overrides() -> None:
    tasks = resolve_tasks({"explain": {"effort": "low"}, "extra": {"model": "other"}})
    assert tasks["explain"] == TaskConfig(effort="low", max_tokens=3000)
    assert tasks["extra"].model == "other"
    assert tasks["grade_sentence"].effort == "high"


def test_every_call_is_logged_with_usage() -> None:
    client, _, records = make_client(response())
    client.generate_exercise(exercise_request())
    (rec,) = records
    assert rec.task == "generate_exercise" and rec.prompt_version == "v2"
    assert rec.model == DEFAULT_MODEL and rec.stop_reason == "end_turn"
    assert (rec.input_tokens, rec.output_tokens) == (11, 7)
    assert (rec.cache_read_tokens, rec.cache_write_tokens) == (5, 3)
    assert rec.response["prompt"] == "Il tavolo." and rec.error is None
    assert rec.request["output_format"] == "GeneratedExercise"


# --- failures ---------------------------------------------------------------------------------


def test_refusal_raises_and_is_logged() -> None:
    res = response(None, stop_reason="refusal")
    res.stop_details = SimpleNamespace(model_dump=lambda mode="json": {"category": "x"})
    client, _, records = make_client(res)
    with pytest.raises(LLMRefusal):
        client.explain(explain_request())
    assert records[0].stop_reason == "refusal"
    assert records[0].response["stop_details"] == {"category": "x"}
    assert records[0].error


def test_max_tokens_is_an_error() -> None:
    client, _, records = make_client(response(None, stop_reason="max_tokens"))
    with pytest.raises(LLMError) as info:
        client.explain(explain_request())
    assert not isinstance(info.value, (LLMRefusal, LLMUnavailable))
    assert records[0].error and records[0].response == "raw text"


def test_missing_parsed_output_is_an_error() -> None:
    client, _, _ = make_client(response(None))
    with pytest.raises(LLMError):
        client.explain(explain_request())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (http_error(anthropic.RateLimitError, 429), LLMUnavailable),
        (http_error(anthropic.InternalServerError, 503), LLMUnavailable),
        (
            anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")),
            LLMUnavailable,
        ),
        (http_error(anthropic.BadRequestError, 400), LLMError),
    ],
)
def test_api_errors_are_mapped_and_logged(error: Exception, expected: type[LLMError]) -> None:
    client, _, records = make_client(error)
    with pytest.raises(expected) as info:
        client.explain(explain_request())
    assert type(info.value) is expected
    assert len(records) == 1 and records[0].error


def test_provider_is_logged_and_params_pass_through() -> None:
    tasks = resolve_tasks({"explain": {"params": {"metadata": {"user_id": "u"}}}})
    sdk = FakeSDK(response())
    records: list[CallRecord] = []
    client = AnthropicLLMClient(sdk, tasks, lambda r: records.append(r) or 1)
    client.explain(explain_request())
    assert sdk.beta.messages.calls[0]["metadata"] == {"user_id": "u"}
    assert records[0].provider == "anthropic"
    assert client.routes["explain"] == f"anthropic/{DEFAULT_MODEL}"
