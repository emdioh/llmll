"""Application settings, read from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLMLL_", populate_by_name=True)

    database_url: str = "sqlite:///./data/llmll.db"
    frontend_dist: Path | None = None
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )
    openrouter_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )
    languagetool_url: str = "http://localhost:8010"
    # Default provider and, for the default provider, its model (required unless `anthropic` or
    # `fake`). Tasks can override both in `llm_tasks`.
    llm_provider: Literal["anthropic", "openai", "google", "openrouter", "fake"] = "anthropic"
    llm_model: str | None = None
    # Optional OpenAI-compatible endpoint, and the OpenRouter attribution headers.
    openai_base_url: str | None = None
    openrouter_app_name: str | None = None
    openrouter_site_url: str | None = None
    llm_refusal_fallback: bool = True
    # SDK-level retries (with back-off) per call, and the HTTP timeout of one request. Set the
    # retries to 0 while diagnosing slowness, so a failing call is not retried silently.
    llm_max_retries: int = 2
    llm_timeout_s: float = 120.0
    # Live LLM debug pane: exposes full prompts and responses, so it is off by default.
    debug: bool = False
    # Name in the contest resolver registry (app.services.contests.RESOLVERS).
    contest_resolver: str = "accept_all"
    # Per-task overrides, e.g. LLMLL_LLM_TASKS='{"grade_sentence": {"effort": "max"}}'.
    # Shared access token; when unset the API is open (local development).
    access_token: SecretStr | None = None
    # Force the Secure flag on the session cookie (use behind an HTTPS reverse proxy).
    cookie_secure: bool = False
    llm_tasks: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator(
        "anthropic_api_key",
        "openai_api_key",
        "gemini_api_key",
        "openrouter_api_key",
        "access_token",
        mode="before",
    )
    @classmethod
    def _clean_secret(cls, value: Any) -> Any:
        """Forgive the usual copy-paste accidents in keys: surrounding whitespace (including a
        Windows `\\r`), and quotes that ended up inside the value. Empty means unset."""
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if not isinstance(value, str):
            return value
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
