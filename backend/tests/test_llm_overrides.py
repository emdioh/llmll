"""LLM model overrides from the Settings page (docs/design/M9-model-settings.md)."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.cli import main as cli_main
from app.config import Settings, get_settings
from app.llm.overrides import OverrideIn, apply_overrides, load_overrides, save_overrides
from app.main import create_app
from app.store.db import create_session_factory
from app.store.models import AppSetting

ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "LLMLL_LLM_PROVIDER",
    "LLMLL_LLM_MODEL",
    "LLMLL_LLM_TASKS",
)
TASKS = ["grade_sentence", "explain", "generate_exercise", "simplify_text", "gloss"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()


def keyed(settings: Settings, **extra: Any) -> Settings:
    update: dict[str, Any] = {
        "anthropic_api_key": SecretStr("dummy"),
        "openrouter_api_key": SecretStr("dummy"),
        **extra,
    }
    return settings.model_copy(update=update)


def plain(**fields: Any) -> Settings:
    return Settings(database_url="sqlite://", **fields)


# --- apply_overrides ---------------------------------------------------------------------


def test_override_replaces_provider_and_model() -> None:
    result = apply_overrides(plain(), {"explain": OverrideIn(provider="openrouter", model="v/m")})
    assert result.llm_tasks == {"explain": {"provider": "openrouter", "model": "v/m"}}


def test_same_provider_keeps_effort_and_params() -> None:
    base = plain(llm_tasks={"explain": {"effort": "high", "params": {"a": 1}, "max_tokens": 10}})
    result = apply_overrides(base, {"explain": OverrideIn(provider="anthropic", model="x")})
    assert result.llm_tasks["explain"] == {
        "effort": "high",
        "params": {"a": 1},
        "max_tokens": 10,
        "provider": "anthropic",
        "model": "x",
    }


def test_different_provider_drops_provider_specific_fields() -> None:
    base = plain(
        llm_tasks={
            "explain": {"effort": "high", "params": {"a": 1}, "max_tokens": 10},
            "gloss": {"provider": "openai", "structured_output": "json"},
        },
        llm_model="gpt-x",
    )
    result = apply_overrides(
        base,
        {
            "explain": OverrideIn(provider="openrouter", model="v/m"),
            "gloss": OverrideIn(provider="google", model="g"),
        },
    )
    assert result.llm_tasks["explain"] == {
        "max_tokens": 10,
        "provider": "openrouter",
        "model": "v/m",
    }
    assert result.llm_tasks["gloss"] == {"provider": "google", "model": "g"}
    # the input is untouched
    assert base.llm_tasks["explain"]["effort"] == "high"


def test_structured_output_passes_and_untouched_tasks_stay() -> None:
    base = plain(llm_tasks={"gloss": {"effort": "low"}})
    result = apply_overrides(
        base, {"explain": OverrideIn(provider="openai", model="m", structured_output="json")}
    )
    assert result.llm_tasks["explain"]["structured_output"] == "json"
    assert result.llm_tasks["gloss"] == {"effort": "low"}


# --- validation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "fake", "model": "m"},
        {"provider": "openai", "model": "   "},
        {"provider": "openai", "model": "x" * 201},
        {"provider": "anthropic", "model": "m", "structured_output": "json"},
        {"provider": "nope", "model": "m"},
    ],
)
def test_invalid_override_bodies(body: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        OverrideIn.model_validate(body)


def test_model_is_stripped() -> None:
    assert OverrideIn(provider="openai", model="  gpt-x ").model == "gpt-x"


def test_load_skips_invalid_entries_and_save_round_trips(migrated_settings: Settings) -> None:
    now = datetime(2026, 10, 5, tzinfo=UTC)
    with create_session_factory(migrated_settings)() as session:
        session.add(
            AppSetting(
                key="llm_overrides",
                value={"explain": {"provider": "fake", "model": "m"}, "bogus": {}},
                updated_at=now,
            )
        )
        session.commit()
        assert load_overrides(session) == {}
        save_overrides(session, {"gloss": OverrideIn(provider="openai", model="m")}, now)
        assert load_overrides(session) == {"gloss": OverrideIn(provider="openai", model="m")}


# --- endpoints ---------------------------------------------------------------------------


def make_client(settings: Settings, **kwargs: Any) -> TestClient:
    return TestClient(create_app(settings, **kwargs))


def stored(settings: Settings) -> dict[str, Any] | None:
    with create_session_factory(settings)() as session:
        row = session.scalars(select(AppSetting)).first()
        return row.value if row else None


def test_get_shape_and_order(migrated_settings: Settings) -> None:
    with make_client(keyed(migrated_settings)) as client:
        body = client.get("/api/settings/llm").json()
    assert body["fake"] is False and body["live_switch"] is True
    assert body["override_error"] is None
    assert body["available_providers"] == ["anthropic", "openrouter"]
    assert [t["task"] for t in body["tasks"]] == TASKS
    first = body["tasks"][0]
    assert first["source"] == "config" and first["override"] is None
    assert first["provider"] == first["config_provider"] == "anthropic"
    assert first["model"] == first["config_model"]


def test_put_switches_health_immediately_and_clears(migrated_settings: Settings) -> None:
    settings = keyed(migrated_settings)
    with make_client(settings) as client:
        response = client.put(
            "/api/settings/llm",
            json={"tasks": {"explain": {"provider": "openrouter", "model": "v/m"}}},
        )
        assert response.status_code == 200
        explain = next(t for t in response.json()["tasks"] if t["task"] == "explain")
        assert explain["source"] == "override" and explain["provider"] == "openrouter"
        assert explain["override"] == {
            "provider": "openrouter",
            "model": "v/m",
            "structured_output": None,
        }
        assert explain["config_provider"] == "anthropic"
        routes = client.get("/api/health").json()["llm_tasks"]
        assert routes["explain"] == "openrouter/v/m"
        assert routes["grade_sentence"].startswith("anthropic/")
        assert stored(settings) == {"explain": {"provider": "openrouter", "model": "v/m"}}

        # a partial update keeps the other overrides
        client.put(
            "/api/settings/llm",
            json={"tasks": {"gloss": {"provider": "openrouter", "model": "v/g"}}},
        )
        assert set(stored(settings) or {}) == {"explain", "gloss"}

        # null clears
        response = client.put("/api/settings/llm", json={"tasks": {"explain": None}})
        assert response.status_code == 200
        assert set(stored(settings) or {}) == {"gloss"}
        routes = client.get("/api/health").json()["llm_tasks"]
        assert routes["explain"].startswith("anthropic/") and routes["gloss"] == "openrouter/v/g"


@pytest.mark.parametrize(
    "tasks",
    [
        {"nope": {"provider": "openrouter", "model": "m"}},
        {"explain": {"provider": "fake", "model": "m"}},
        {"explain": {"provider": "openrouter", "model": ""}},
        {"explain": {"provider": "anthropic", "model": "m", "structured_output": "json"}},
        # an OpenRouter id used with another provider
        {"explain": {"provider": "anthropic", "model": "vendor/model"}},
        # provider without a key
        {"explain": {"provider": "google", "model": "gemini-x"}},
    ],
)
def test_invalid_put_is_422_and_stores_nothing(
    migrated_settings: Settings, tasks: dict[str, Any]
) -> None:
    with make_client(keyed(migrated_settings)) as client:
        before = client.get("/api/health").json()["llm_tasks"]
        response = client.put("/api/settings/llm", json={"tasks": tasks})
        assert response.status_code == 422
        assert client.get("/api/health").json()["llm_tasks"] == before
    assert stored(migrated_settings) is None


def test_fake_fallback_is_reported_and_put_conflicts(migrated_settings: Settings) -> None:
    with make_client(migrated_settings) as client:
        assert client.get("/api/settings/llm").json()["fake"] is True
        response = client.put(
            "/api/settings/llm",
            json={"tasks": {"explain": {"provider": "openrouter", "model": "v/m"}}},
        )
        assert response.status_code == 409
    assert stored(migrated_settings) is None


def test_injected_client_validates_and_stores_but_cannot_swap(
    migrated_settings: Settings,
) -> None:
    from app.llm.fake import FakeLLMClient

    settings = keyed(migrated_settings)
    with make_client(settings, llm=FakeLLMClient(lambda r: None)) as client:
        assert client.get("/api/settings/llm").json()["live_switch"] is False
        ok = client.put(
            "/api/settings/llm",
            json={"tasks": {"explain": {"provider": "openrouter", "model": "v/m"}}},
        )
        assert ok.status_code == 200
        bad = client.put(
            "/api/settings/llm",
            json={"tasks": {"gloss": {"provider": "anthropic", "model": "a/b"}}},
        )
        assert bad.status_code == 422
        assert client.get("/api/health").json()["llm"] == "fake"
    assert set(stored(settings) or {}) == {"explain"}


def test_requires_auth_like_other_settings(migrated_settings: Settings) -> None:
    settings = keyed(migrated_settings, access_token=SecretStr("tok"))
    with make_client(settings) as client:
        assert client.get("/api/settings/llm").status_code == 401


# --- startup -----------------------------------------------------------------------------


def seed(settings: Settings, value: dict[str, Any]) -> None:
    with create_session_factory(settings)() as session:
        session.add(
            AppSetting(
                key="llm_overrides", value=value, updated_at=datetime(2026, 10, 5, tzinfo=UTC)
            )
        )
        session.commit()


def test_stored_overrides_are_applied_on_a_new_app(migrated_settings: Settings) -> None:
    settings = keyed(migrated_settings)
    seed(settings, {"gloss": {"provider": "openrouter", "model": "v/g"}})
    with make_client(settings) as client:
        assert client.get("/api/health").json()["llm_tasks"]["gloss"] == "openrouter/v/g"
        assert client.get("/api/settings/llm").json()["override_error"] is None


def test_stored_override_without_its_key_falls_back_and_reports(
    migrated_settings: Settings,
) -> None:
    settings = keyed(migrated_settings, openrouter_api_key=None)
    seed(settings, {"gloss": {"provider": "openrouter", "model": "v/g"}})
    with make_client(settings) as client:
        health = client.get("/api/health").json()
        assert health["llm_tasks"]["gloss"].startswith("anthropic/")
        body = client.get("/api/settings/llm").json()
        assert "OPENROUTER_API_KEY" in body["override_error"]
        gloss = next(t for t in body["tasks"] if t["task"] == "gloss")
        assert gloss["provider"] == "anthropic" and gloss["override"] is not None
        # clearing the broken override recovers
        ok = client.put("/api/settings/llm", json={"tasks": {"gloss": None}})
        assert ok.status_code == 200 and ok.json()["override_error"] is None


def test_outdated_schema_skips_the_stored_overrides(settings: Settings) -> None:
    with make_client(keyed(settings)) as client:  # database never migrated
        assert client.get("/api/health").json()["llm_tasks"]["gloss"].startswith("anthropic/")


# --- CLI ---------------------------------------------------------------------------------


def test_check_llm_shows_a_stored_override(
    monkeypatch: pytest.MonkeyPatch, migrated_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLMLL_DATABASE_URL", migrated_settings.database_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
    seed(migrated_settings, {"gloss": {"provider": "openrouter", "model": "v/g"}})
    cli_main(["check-llm"])
    out = capsys.readouterr().out
    assert "openrouter/v/g" in out and "(Settings)" in out
    gloss_line = next(line for line in out.splitlines() if line.strip().startswith("gloss"))
    explain_line = next(line for line in out.splitlines() if line.strip().startswith("explain"))
    assert "(Settings)" in gloss_line and "(Settings)" not in explain_line


def test_check_llm_with_an_unmigrated_database_notes_and_continues(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLMLL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc")
    cli_main(["check-llm"])
    captured = capsys.readouterr()
    assert "anthropic/" in captured.out and "note:" in captured.err
