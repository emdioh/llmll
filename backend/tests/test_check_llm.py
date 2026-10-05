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
            content="{}", refusal=None, parsed=Gloss(translation="casa", lemma="Haus")
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=None),
        )

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))),
    )
    monkeypatch.setenv("LLMLL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLMLL_LLM_MODEL", "vendor/model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
    assert cli_main(["check-llm", "--call"]) == 0
    assert "OK in" in capsys.readouterr().out
