"""Key cleaning in settings and the `check-llm` diagnostic command."""

from types import SimpleNamespace

import pytest

from app.cli import describe_key
from app.cli import main as cli_main
from app.config import Settings, get_settings

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


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LLMLL_DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "raw",
    ["sk-or-v1-abc", " sk-or-v1-abc ", "sk-or-v1-abc\r", '"sk-or-v1-abc"', "'sk-or-v1-abc'\n"],
)
def test_keys_are_cleaned(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", raw)
    key = Settings().openrouter_api_key
    assert key is not None and key.get_secret_value() == "sk-or-v1-abc"


def test_blank_key_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "  ")
    assert Settings().openrouter_api_key is None


def test_describe_key_never_reveals_the_key() -> None:
    facts = " ".join(describe_key("openrouter", "sk-or-v1-0123456789abcdef"))
    assert "0123456789abcdef" not in facts and "25 characters" in facts
    assert "PROBLEM" in " ".join(describe_key("openrouter", "sk-or-v1-<...>"))
    assert "PROBLEM" in " ".join(describe_key("openrouter", "sk-ant-wrong-provider"))
    assert "PROBLEM" in " ".join(describe_key("anthropic", "sk-ant- with space"))


def test_check_llm_reports_missing_key_and_wrong_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLMLL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLMLL_LLM_MODEL", "vendor/model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-or-v1-abc")
    assert cli_main(["check-llm"]) == 1
    out = capsys.readouterr().out
    assert "OPENROUTER_API_KEY is not set" in out and "OPENAI_API_KEY" in out
    assert "openrouter/vendor/model" in out


def test_check_llm_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import openai

    from app.llm.types import Gloss

    def parse(**kwargs):  # type: ignore[no-untyped-def]
        message = SimpleNamespace(
            content=Gloss(translation="casa", lemma="Haus").model_dump_json(), refusal=None
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=None),
        )

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **_: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=parse))
        ),
    )
    monkeypatch.setenv("LLMLL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLMLL_LLM_MODEL", "vendor/model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
    assert cli_main(["check-llm", "--call"]) == 0
    assert "OK in" in capsys.readouterr().out


def test_check_llm_call_repeat_shows_the_breakdown_and_p50(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import httpx2
    import openai

    from app.llm.types import Gloss

    body = {
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "provider": "SomeUpstream",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": Gloss(translation="casa", lemma="Haus").model_dump_json(),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 9,
            "total_tokens": 14,
            "completion_tokens_details": {"reasoning_tokens": 4},
        },
    }
    real = openai.OpenAI

    def with_mock_transport(**kwargs):  # type: ignore[no-untyped-def]
        # keep the app's own http client (and its tracing hooks), swap only the transport
        hooks = kwargs["http_client"].event_hooks
        kwargs["http_client"] = openai.DefaultHttpxClient(
            transport=httpx2.MockTransport(lambda r: httpx2.Response(200, json=body)),
            event_hooks=hooks,
        )
        return real(**kwargs)

    monkeypatch.setattr(openai, "OpenAI", with_mock_transport)
    monkeypatch.setenv("LLMLL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLMLL_LLM_MODEL", "vendor/model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
    assert cli_main(["check-llm", "--call", "--repeat", "3"]) == 0
    out = capsys.readouterr().out
    assert "3 times" in out and "#1 OK" in out and "#3 OK" in out
    assert out.count("= wait 0.0s + ttfb") == 3 and "1 try" in out and "reasoning 4" in out
    assert "upstream provider: SomeUpstream" in out and "p50" in out
