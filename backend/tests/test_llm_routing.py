"""Provider selection, per-task routing and configuration errors (docs/design/M6-providers.md)."""

import logging
from typing import Any

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.calls import CallRecord
from app.llm.config import DEFAULT_MODEL
from app.llm.factory import LLMConfigError, RoutingLLMClient, build_llm_client_with_recorder
from app.llm.fake import FakeLLMClient
from app.llm.google_client import GeminiLLMClient
from app.llm.openai_client import OPENROUTER_BASE_URL, OpenAICompatibleLLMClient
from app.main import create_app

from .llm_helpers import exercise_request, explain_request, grade_request

KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "LLMLL_LLM_PROVIDER",
    "LLMLL_LLM_MODEL",
    "LLMLL_LLM_TASKS",
)
TASKS = ("generate_exercise", "grade_sentence", "explain", "simplify_text", "gloss")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def settings(**fields: Any) -> Settings:
    for name in ("anthropic", "openai", "gemini", "openrouter"):
        if isinstance(fields.get(f"{name}_api_key"), str):
            fields[f"{name}_api_key"] = SecretStr(fields[f"{name}_api_key"])
    return Settings(database_url="sqlite://", **fields)


def build(**fields: Any):  # type: ignore[no-untyped-def]
    records: list[CallRecord] = []
    return build_llm_client_with_recorder(settings(**fields), lambda r: records.append(r))


# --- single provider ------------------------------------------------------------------------


def test_no_key_at_all_falls_back_to_fake(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        client = build()
    assert isinstance(client, FakeLLMClient)
    assert "no LLM API key" in caplog.text
    # Also when a non-Anthropic provider is the default (the model is still required).
    assert isinstance(build(llm_provider="openai", llm_model="m"), FakeLLMClient)


def test_anthropic_keeps_its_default_model() -> None:
    client = build(anthropic_api_key="k")
    assert isinstance(client, AnthropicLLMClient) and client.name == "anthropic"
    assert client.routes == {task: f"anthropic/{DEFAULT_MODEL}" for task in TASKS}
    assert (
        build(anthropic_api_key="k", llm_model="claude-x").routes["gloss"] == "anthropic/claude-x"
    )


def test_openai() -> None:
    client = build(llm_provider="openai", llm_model="gpt-x", openai_api_key="k")
    assert isinstance(client, OpenAICompatibleLLMClient) and client.name == "openai"
    assert client.routes == {task: "openai/gpt-x" for task in TASKS}
    assert "api.openai.com" in str(client._client.base_url)


def test_openai_custom_base_url() -> None:
    client = build(
        llm_provider="openai",
        llm_model="m",
        openai_api_key="k",
        openai_base_url="http://localhost:8000/v1",
    )
    assert str(client._client.base_url).startswith("http://localhost:8000/v1")


def test_openrouter_headers() -> None:
    client = build(
        llm_provider="openrouter",
        llm_model="vendor/model",
        openrouter_api_key="k",
        openrouter_app_name="LLMLL",
        openrouter_site_url="https://example.org",
    )
    assert isinstance(client, OpenAICompatibleLLMClient) and client.name == "openrouter"
    assert str(client._client.base_url).rstrip("/") == OPENROUTER_BASE_URL
    headers = client._client.default_headers
    assert headers["X-Title"] == "LLMLL" and headers["HTTP-Referer"] == "https://example.org"
    plain = build(llm_provider="openrouter", llm_model="m", openrouter_api_key="k")
    assert "X-Title" not in plain._client.default_headers
    assert "HTTP-Referer" not in plain._client.default_headers


def test_google_and_key_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build(llm_provider="google", llm_model="gemini-x", gemini_api_key="k")
    assert isinstance(client, GeminiLLMClient) and client.name == "google"
    assert client.routes["grade_sentence"] == "google/gemini-x"
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(var, "from-env")
        assert Settings(database_url="sqlite://").gemini_api_key == SecretStr("from-env")
        monkeypatch.delenv(var)


def test_explicit_fake_ignores_keys() -> None:
    assert isinstance(build(llm_provider="fake", anthropic_api_key="k"), FakeLLMClient)


# --- per-task routing -------------------------------------------------------------------------


class Stub:
    """Records which task reached it."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    def __getattr__(self, task: str) -> Any:
        def call(req: Any) -> str:
            self.calls.append(task)
            return f"{self.name}:{task}"

        return call


def test_per_task_resolution_and_dispatch() -> None:
    client = build(
        anthropic_api_key="a",
        openrouter_api_key="o",
        gemini_api_key="g",
        llm_tasks={
            "grade_sentence": {"provider": "anthropic", "effort": "max"},
            "gloss": {"provider": "openrouter", "model": "vendor/model"},
            "simplify_text": {"provider": "google", "model": "gemini-x"},
        },
    )
    assert isinstance(client, RoutingLLMClient) and client.name == "anthropic"
    assert client.routes == {
        "generate_exercise": f"anthropic/{DEFAULT_MODEL}",
        "grade_sentence": f"anthropic/{DEFAULT_MODEL}",
        "explain": f"anthropic/{DEFAULT_MODEL}",
        "simplify_text": "google/gemini-x",
        "gloss": "openrouter/vendor/model",
    }
    stubs = {name: Stub(name) for name in ("anthropic", "google", "openrouter")}
    client._clients = stubs  # type: ignore[assignment]
    assert client.explain(explain_request()) == "anthropic:explain"
    assert client.gloss(None) == "openrouter:gloss"  # type: ignore[arg-type]
    assert client.simplify_text(None) == "google:simplify_text"  # type: ignore[arg-type]
    assert client.generate_exercise(exercise_request()) == "anthropic:generate_exercise"
    assert client.grade_sentence(grade_request()) == "anthropic:grade_sentence"
    assert stubs["anthropic"].calls == ["explain", "generate_exercise", "grade_sentence"]


def test_task_overrides_default_provider_and_model() -> None:
    client = build(
        llm_provider="openai",
        llm_model="gpt-x",
        openai_api_key="o",
        anthropic_api_key="a",
        llm_tasks={"grade_sentence": {"provider": "anthropic"}, "explain": {"model": "gpt-y"}},
    )
    assert isinstance(client, RoutingLLMClient) and client.name == "openai"
    assert client.routes["grade_sentence"] == f"anthropic/{DEFAULT_MODEL}"
    assert client.routes["explain"] == "openai/gpt-y"
    assert client.routes["gloss"] == "openai/gpt-x"
    openai_client = client._clients["openai"]
    assert openai_client._tasks["explain"].model == "gpt-y"


def test_all_tasks_on_one_non_default_provider_is_a_single_client() -> None:
    overrides = {t: {"provider": "google", "model": "gemini-x"} for t in TASKS}
    client = build(gemini_api_key="g", llm_tasks=overrides)
    assert isinstance(client, GeminiLLMClient)


def test_effort_is_ignored_with_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        build(
            llm_provider="openai",
            llm_model="m",
            openai_api_key="k",
            llm_tasks={"grade_sentence": {"effort": "max"}},
        )
    assert caplog.text.count("'effort' only applies to Anthropic") == 1


# --- configuration errors ---------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["openai", "google", "openrouter"])
def test_provider_without_model_is_a_startup_error(provider: str) -> None:
    keys = {"openai_api_key": "k", "gemini_api_key": "k", "openrouter_api_key": "k"}
    with pytest.raises(LLMConfigError, match="no model is configured") as info:
        build(llm_provider=provider, **keys)
    assert "LLMLL_LLM_MODEL" in str(info.value)


def test_task_provider_without_model_is_a_startup_error() -> None:
    with pytest.raises(LLMConfigError, match="'gloss'.*'openrouter'"):
        build(
            anthropic_api_key="a",
            openrouter_api_key="o",
            llm_tasks={"gloss": {"provider": "openrouter"}},
        )


def test_global_model_does_not_apply_to_other_providers() -> None:
    with pytest.raises(LLMConfigError, match="'gloss'.*'google'"):
        build(
            llm_provider="openai",
            llm_model="gpt-x",
            openai_api_key="o",
            gemini_api_key="g",
            llm_tasks={"gloss": {"provider": "google"}},
        )


def test_missing_key_for_a_configured_provider_is_a_startup_error() -> None:
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
        build(llm_provider="openai", llm_model="m", anthropic_api_key="a")
    with pytest.raises(LLMConfigError, match="GEMINI_API_KEY"):
        build(anthropic_api_key="a", llm_tasks={"gloss": {"provider": "google", "model": "m"}})


def test_invalid_task_configuration_is_a_startup_error() -> None:
    with pytest.raises(LLMConfigError, match="LLMLL_LLM_TASKS"):
        build(anthropic_api_key="a", llm_tasks={"gloss": {"provider": "mistral"}})
    with pytest.raises(LLMConfigError, match="LLMLL_LLM_TASKS"):
        build(anthropic_api_key="a", llm_tasks={"gloss": {"structured_output": "xml"}})


def test_json_mode_is_rejected_for_anthropic() -> None:
    with pytest.raises(LLMConfigError, match="anthropic"):
        build(anthropic_api_key="a", llm_tasks={"gloss": {"structured_output": "json"}})


def test_app_startup_fails_clearly_without_a_model() -> None:
    config = settings(llm_provider="openai", openai_api_key="k")
    with pytest.raises(LLMConfigError, match="LLMLL_LLM_MODEL"):
        create_app(config)


# --- provider/model mix-ups -----------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,key", [("google", "gemini"), ("anthropic", "anthropic"), ("openai", "openai")]
)
def test_openrouter_style_id_with_other_provider_is_rejected(provider: str, key: str) -> None:
    with pytest.raises(LLMConfigError, match="looks like an OpenRouter id"):
        build(
            llm_provider=provider,
            llm_model="anthropic/some-model",
            **{f"{key}_api_key": "k"},
        )


def test_slashes_allowed_where_valid() -> None:
    build(llm_provider="openrouter", llm_model="vendor/model", openrouter_api_key="k")
    build(llm_provider="google", llm_model="models/some-gemini", gemini_api_key="k")
    build(
        llm_provider="openai",
        llm_model="org/model",
        openai_api_key="k",
        openai_base_url="https://gateway.example/v1",
    )
