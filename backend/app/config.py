"""Application settings, read from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLMLL_", populate_by_name=True)

    database_url: str = "sqlite:///./data/llmll.db"
    frontend_dist: Path | None = None
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    languagetool_url: str = "http://localhost:8010"


@lru_cache
def get_settings() -> Settings:
    return Settings()
