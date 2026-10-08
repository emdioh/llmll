"""HTTP tracing: timing derivation, hooks + context var, and the three SDKs end to end."""

import threading
import time
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import httpx2
import openai
import pytest
from google import genai
from google.genai import types as genai_types
from pydantic import SecretStr

from app.config import Settings
from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.calls import CallRecord
from app.llm.client import LLMUnavailable
from app.llm.config import resolve_tasks
from app.llm.factory import _build_provider
from app.llm.google_client import GeminiLLMClient
from app.llm.openai_client import OpenAICompatibleLLMClient
from app.llm.trace import HttpAttempt, derive_timing, trace_call, tracing_hooks
from app.llm.types import Gloss, GlossRequest

GLOSS = Gloss(translation="casa", lemma="Haus")
REQUEST = GlossRequest(
    word="Haus", lemma="Haus", sentence="Das Haus.", level="A2", explanation_language="it"
)


# --- pure derivation -----------------------------------------------------------------------------


def test_single_attempt() -> None:
    timing = derive_timing([HttpAttempt(10.0, 12.5, 200)], ended=12.7, latency_ms=2800)
    assert timing is not None
    assert timing.attempts == 1 and timing.http_statuses == [200]
    assert timing.retry_wait_ms == 0 and timing.ttfb_ms == 2500 and timing.download_ms == 200
    assert timing.overhead_ms == 100  # 2800 - (0 + 2500 + 200)


def test_retry_after_429_with_backoff() -> None:
    attempts = [HttpAttempt(0.0, 0.2, 429), HttpAttempt(1.5, 4.0, 200)]
    timing = derive_timing(attempts, ended=4.1, latency_ms=4100)
    assert timing is not None
    assert timing.attempts == 2 and timing.http_statuses == [429, 200]
    assert timing.retry_wait_ms == 1500  # failed attempt + back-off before the final request
    assert timing.ttfb_ms == 2500 and timing.download_ms == 100 and timing.overhead_ms == 0


def test_failure_without_a_response_cannot_be_attributed() -> None:
    attempts = [HttpAttempt(0.0, None, None), HttpAttempt(0.7, None, None)]
    timing = derive_timing(attempts, ended=1.0, latency_ms=1000)
    assert timing is not None
    assert timing.http_statuses == [None, None] and timing.retry_wait_ms == 700
    assert timing.ttfb_ms is None and timing.download_ms is None and timing.overhead_ms is None


def test_failure_after_retries_keeps_the_statuses() -> None:
    attempts = [HttpAttempt(0.0, 0.1, 500), HttpAttempt(0.6, 0.7, 500)]
    timing = derive_timing(attempts, ended=0.75, latency_ms=750)
    assert timing is not None and timing.http_statuses == [500, 500]
    assert timing.overhead_ms == 0


def test_overhead_is_floored_at_zero() -> None:
    # rounding can make the parts exceed the measured latency by a millisecond
    timing = derive_timing([HttpAttempt(0.0, 0.5, 200)], ended=1.0, latency_ms=990)
    assert timing is not None and timing.overhead_ms == 0


def test_no_http_attempts_means_no_timing() -> None:
    assert derive_timing([], ended=1.0, latency_ms=1000) is None


# --- hooks and the context var -------------------------------------------------------------------


def mock_client(handler: Any) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler), event_hooks=tracing_hooks())


def test_hooks_record_attempts_on_the_current_trace() -> None:
    answers = iter([429, 200])

    def handler(request: httpx2.Request) -> httpx2.Response:
        time.sleep(0.02)
        return httpx2.Response(next(answers), json={})

    with mock_client(handler) as client, trace_call() as trace:
        client.get("http://test/a")
        client.get("http://test/b")
    assert [a.status for a in trace.attempts] == [429, 200]
    first, second = trace.attempts
    assert first.request_at < first.headers_at <= second.request_at < second.headers_at  # type: ignore[operator]
    assert trace.ended >= second.headers_at  # type: ignore[operator]


def test_requests_outside_a_trace_are_ignored() -> None:
    with mock_client(lambda r: httpx2.Response(200)) as client:
        assert client.get("http://test/").status_code == 200


def test_traces_are_per_thread() -> None:
    barrier = threading.Barrier(2)
    seen: dict[str, list[int | None]] = {}

    def worker(name: str, calls: int) -> None:
        with mock_client(lambda r: httpx2.Response(200)) as client, trace_call() as trace:
            barrier.wait(timeout=5)
            for _ in range(calls):
                client.get("http://test/")
        seen[name] = [a.status for a in trace.attempts]

    threads = [
        threading.Thread(target=worker, args=("a", 1)),
        threading.Thread(target=worker, args=("b", 3)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert seen == {"a": [200], "b": [200, 200, 200]}


def test_a_failing_hook_never_breaks_the_request() -> None:
    hooks = tracing_hooks()
    # a response object without `status_code` makes the hook fail internally
    with trace_call() as trace:
        trace.attempts.append(HttpAttempt(0.0))
        hooks["response"][0](SimpleNamespace())
    assert trace.attempts[0].status is None


# --- the SDKs end to end (mock transports) -------------------------------------------------------


def completion_json(**extra: Any) -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": GLOSS.model_dump_json()},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
        **extra,
    }


def test_openai_sdk_retry_is_traced_on_the_right_call() -> None:
    answers = [
        httpx2.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "slow"}}),
        httpx2.Response(200, json=completion_json(provider="SomeUpstream")),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return answers.pop(0)

    sdk = openai.OpenAI(
        api_key="k",
        base_url="http://test/v1",
        max_retries=2,
        http_client=openai.DefaultHttpxClient(
            transport=httpx2.MockTransport(handler), event_hooks=tracing_hooks()
        ),
    )
    records: list[CallRecord] = []
    client = OpenAICompatibleLLMClient(
        sdk,
        "openrouter",
        resolve_tasks({}),
        lambda r: records.append(r),  # type: ignore[arg-type,func-returns-value]
    )
    assert client.gloss(REQUEST).translation == "casa"
    (record,) = records
    assert record.attempts == 2 and record.http_statuses == [429, 200]
    assert record.retry_wait_ms is not None and record.ttfb_ms is not None
    assert record.download_ms is not None and record.overhead_ms is not None
    assert record.reasoning_tokens == 3 and record.upstream_provider == "SomeUpstream"
    assert record.input_tokens == 10 and record.output_tokens == 5
    assert record.request_chars and record.request_chars > 100


def test_openai_sdk_failure_keeps_the_statuses() -> None:
    sdk = openai.OpenAI(
        api_key="k",
        base_url="http://test/v1",
        max_retries=1,
        http_client=openai.DefaultHttpxClient(
            transport=httpx2.MockTransport(
                lambda r: httpx2.Response(503, headers={"retry-after": "0"}, json={})
            ),
            event_hooks=tracing_hooks(),
        ),
    )
    records: list[CallRecord] = []
    client = OpenAICompatibleLLMClient(
        sdk,
        "openai",
        resolve_tasks({}),
        lambda r: records.append(r),  # type: ignore[arg-type,func-returns-value]
    )
    with pytest.raises(LLMUnavailable):
        client.gloss(REQUEST)
    (record,) = records
    assert record.http_statuses == [503, 503] and record.error
    assert record.latency_ms >= record.retry_wait_ms  # type: ignore[operator]


def test_anthropic_sdk_is_traced() -> None:
    body = {
        "id": "m1",
        "type": "message",
        "role": "assistant",
        "model": "claude-x",
        "content": [{"type": "text", "text": GLOSS.model_dump_json()}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 9, "output_tokens": 4},
    }
    sdk = anthropic.Anthropic(
        api_key="k",
        base_url="http://test",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(lambda r: httpx2.Response(200, json=body)),
            event_hooks=tracing_hooks(),
        ),
    )
    records: list[CallRecord] = []
    client = AnthropicLLMClient(
        sdk,
        resolve_tasks({}),
        lambda r: records.append(r),
        refusal_fallback=False,  # type: ignore[arg-type,func-returns-value]
    )
    assert client.gloss(REQUEST).translation == "casa"
    (record,) = records
    assert record.attempts == 1 and record.http_statuses == [200]
    assert record.reasoning_tokens is None and record.upstream_provider is None
    assert record.request_chars and record.request_chars > 100


def test_gemini_sdk_is_traced_through_its_httpx_client() -> None:
    body = {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": GLOSS.model_dump_json()}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 6,
            "candidatesTokenCount": 4,
            "thoughtsTokenCount": 9,
        },
    }
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
        event_hooks=tracing_hooks(),
    )
    sdk = genai.Client(
        api_key="k", http_options=genai_types.HttpOptions(httpx_client=http, timeout=5000)
    )
    records: list[CallRecord] = []
    client = GeminiLLMClient(sdk, resolve_tasks({}), lambda r: records.append(r))  # type: ignore[arg-type,func-returns-value]
    assert client.gloss(REQUEST).translation == "casa"
    (record,) = records
    assert record.attempts == 1 and record.http_statuses == [200]
    assert record.reasoning_tokens == 9 and record.output_tokens == 4


def test_factory_clients_carry_the_hooks_and_the_configured_timeout() -> None:
    settings = Settings(
        database_url="sqlite://",
        llm_timeout_s=33.0,
        llm_max_retries=0,
        openai_api_key=SecretStr("k"),
    )
    tasks = resolve_tasks({})
    for provider in ("openai", "anthropic"):
        client = _build_provider(provider, settings, "k", tasks, lambda r: None)
        sdk = client._client
        assert sdk._client.event_hooks["request"] and sdk._client.event_hooks["response"]
        assert sdk.timeout == 33.0 and sdk.max_retries == 0
    google = _build_provider("google", settings, "k", tasks, lambda r: None)
    api = google._client._api_client
    assert api._httpx_client.event_hooks["response"]
    assert api._http_options.timeout == 33000


def test_adapters_extract_reasoning_and_upstream_provider() -> None:
    usage = SimpleNamespace(
        prompt_tokens=1,
        completion_tokens=2,
        prompt_tokens_details=None,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=7),
    )
    message = SimpleNamespace(content="{}", refusal=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    done = OpenAICompatibleLLMClient._normalize(
        SimpleNamespace(choices=[choice], usage=usage, provider="Fireworks")
    )
    assert done.reasoning_tokens == 7 and done.upstream_provider == "Fireworks"
    # the SDK keeps unknown fields in `model_extra`
    extra = openai.types.chat.ChatCompletion.model_validate(completion_json(provider="Together"))
    assert OpenAICompatibleLLMClient._normalize(extra).upstream_provider == "Together"
    plain = OpenAICompatibleLLMClient._normalize(
        SimpleNamespace(
            choices=[choice], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2)
        )
    )
    assert plain.reasoning_tokens is None and plain.upstream_provider is None
