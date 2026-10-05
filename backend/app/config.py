"""Application settings, read from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLMLL_", populate_by_name=True)

    database_url: str = "sqlite:///./data/llmll.db"
    frontend_dist: Path | None = None
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    languagetool_url: str = "http://localhost:8010"
    llm_provider: Literal["anthropic", "fake"] = "anthropic"
    llm_refusal_fallback: bool = True
    llm_max_retries: int = 2
    # Name in the contest resolver registry (app.services.contests.RESOLVERS).
    contest_resolver: str = "accept_all"
    # Per-task overrides, e.g. LLMLL_LLM_TASKS='{"grade_sentence": {"effort": "max"}}'.
    llm_tasks: dict[str, dict[str, Any]] = Field(default_factory=dict)


@lru_cache
def get_settings() -> Settings:
    return Settings()
