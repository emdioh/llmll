"""Gemini adapter with a mocked `google-genai` client."""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors, types

from app.llm.calls import CallRecord
from app.llm.client import LLMError, LLMRefusal, LLMUnavailable
from app.llm.config import resolve_tasks
from app.llm.google_client import GeminiLLMClient
from app.llm.types import GeneratedExercise, GradeResult

from .llm_helpers import exercise_request, explain_request, grade_request

GENERATED = GeneratedExercise(
    instructions="Traduci.",
    prompt="Il tavolo.",
    glossary=[],
    reference_solutions=["Der Tisch."],
    targets=[],
)


def response(
    parsed: Any = GENERATED,
    finish: Any = types.FinishReason.STOP,
    text: str | None = "raw text",
    block_reason: Any = None,
) -> SimpleNamespace:
    candidate = SimpleNamespace(
        finish_reason=finish, content=SimpleNamespace(parts=[SimpleNamespace(text=text)])
    )
    return SimpleNamespace(
        candidates=[candidate],
        parsed=parsed,
        prompt_feedback=SimpleNamespace(block_reason=block_reason, block_reason_message=None)
        if block_reason
        else None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=11, candidates_token_count=7, cached_content_token_count=5
        ),
    )


class FakeModels:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


def make_client(
    *results: Any, overrides: dict[str, dict[str, Any]] | None = None
) -> tuple[GeminiLLMClient, FakeModels, list[CallRecord]]:
    models = FakeModels(list(results))
    records: list[CallRecord] = []

    def record(rec: CallRecord) -> int:
        records.append(rec)
        return len(records)

    base = {t: {"provider": "google", "model": "gemini-x"} for t in resolve_tasks({})}
    for task, fields in (overrides or {}).items():
        base[task] = {**base[task], **fields}
    client = GeminiLLMClient(SimpleNamespace(models=models), resolve_tasks(base), record)
    return client, models, records


def test_request_shape_native() -> None:
    client, sdk, _ = make_client(response())
    assert client.generate_exercise(exercise_request()) == GENERATED
    (call,) = sdk.calls
    assert call["model"] == "gemini-x"
    stable, variable = call["contents"]
    assert "gram:cases" in stable and "Exercise type: translation" in variable
    config = call["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert "German" in str(config.system_instruction)
    assert config.response_mime_type == "application/json"
    assert config.response_schema is GeneratedExercise
    assert config.max_output_tokens == 4000


def test_params_pass_through() -> None:
    overrides = {"grade_sentence": {"params": {"thinking_config": {"thinking_budget": 0}}}}
    graded = GradeResult(overall="correct", corrected_sentence="x", feedback="y")
    client, sdk, _ = make_client(response(graded), overrides=overrides)
    client.grade_sentence(grade_request())
    assert sdk.calls[0]["config"].thinking_config.thinking_budget == 0


def test_every_call_is_logged_with_provider_and_usage() -> None:
    client, _, records = make_client(response())
    client.generate_exercise(exercise_request())
    (rec,) = records
    assert rec.provider == "google" and rec.model == "gemini-x"
    assert rec.stop_reason == "STOP"
    assert (rec.input_tokens, rec.output_tokens, rec.cache_read_tokens) == (11, 7, 5)
    assert rec.response["prompt"] == "Il tavolo." and rec.error is None
    assert rec.request["config"]["response_schema"] == "GeneratedExercise"
    assert rec.request["config"]["system_instruction"]


def test_blocked_prompt_is_a_refusal() -> None:
    res = SimpleNamespace(
        candidates=None,
        parsed=None,
        prompt_feedback=SimpleNamespace(
            block_reason=types.BlockedReason.SAFETY, block_reason_message="no"
        ),
        usage_metadata=None,
    )
    client, _, records = make_client(res)
    with pytest.raises(LLMRefusal):
        client.explain(explain_request())
    assert records[0].stop_reason == "blocked" and records[0].error


@pytest.mark.parametrize("finish", [types.FinishReason.SAFETY, "PROHIBITED_CONTENT"])
def test_safety_finish_is_a_refusal(finish: Any) -> None:
    client, _, records = make_client(response(None, finish=finish, text=None))
    with pytest.raises(LLMRefusal):
        client.explain(explain_request())
    assert records[0].error


def test_max_tokens_is_an_error() -> None:
    client, _, records = make_client(response(None, finish=types.FinishReason.MAX_TOKENS))
    with pytest.raises(LLMError, match="truncated") as info:
        client.explain(explain_request())
    assert not isinstance(info.value, (LLMRefusal, LLMUnavailable))
    assert records[0].response == "raw text"


def test_missing_parsed_output_is_an_error() -> None:
    client, _, _ = make_client(response(None))
    with pytest.raises(LLMError, match="no structured output"):
        client.explain(explain_request())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (errors.ClientError(429, {"error": {"message": "slow down"}}), LLMUnavailable),
        (errors.ServerError(503, {"error": {"message": "overloaded"}}), LLMUnavailable),
        (httpx.ConnectError("down"), LLMUnavailable),
        (errors.ClientError(400, {"error": {"message": "bad"}}), LLMError),
        (errors.ClientError(403, {"error": {"message": "key"}}), LLMError),
    ],
)
def test_api_errors_are_mapped_and_logged(error: Exception, expected: type[LLMError]) -> None:
    client, _, records = make_client(error)
    with pytest.raises(expected) as info:
        client.explain(explain_request())
    assert type(info.value) is expected
    assert len(records) == 1 and records[0].error and records[0].provider == "google"


# --- json structured-output mode -----------------------------------------------------------

JSON_OVERRIDES = {"generate_exercise": {"structured_output": "json"}}
VALID_JSON = GENERATED.model_dump_json()


def test_json_mode_valid_reply() -> None:
    client, sdk, _ = make_client(response(None, text=VALID_JSON), overrides=JSON_OVERRIDES)
    assert client.generate_exercise(exercise_request()) == GENERATED
    config = sdk.calls[0]["config"]
    assert config.response_schema is None and config.response_mime_type == "application/json"
    assert "JSON schema" in str(config.system_instruction)


def test_json_mode_retries_once_with_the_validation_error() -> None:
    client, sdk, records = make_client(
        response(None, text="{}"), response(None, text=VALID_JSON), overrides=JSON_OVERRIDES
    )
    assert client.generate_exercise(exercise_request()) == GENERATED
    first, second = sdk.calls
    assert len(first["contents"]) == 2 and "rejected" not in first["contents"][-1]
    assert "rejected" in second["contents"][-1] and "Field required" in second["contents"][-1]
    assert [r.error is None for r in records] == [False, True]


def test_json_mode_invalid_twice_is_an_error() -> None:
    client, sdk, records = make_client(response(None, text="nope"), overrides=JSON_OVERRIDES)
    with pytest.raises(LLMError, match="invalid structured output"):
        client.generate_exercise(exercise_request())
    assert len(sdk.calls) == 2 and len(records) == 2
