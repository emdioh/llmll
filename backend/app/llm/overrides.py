"""LLM model overrides chosen from the Settings page (design: docs/design/M9-model-settings.md).

Precedence: built-in defaults < environment < these overrides < CLI flags of one command.
"""

import logging
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm.config import DEFAULT_TASKS
from app.store.models import AppSetting

logger = logging.getLogger(__name__)

SETTINGS_KEY = "llm_overrides"
# The order the Settings page shows them in.
TASK_ORDER: tuple[str, ...] = (
    "grade_sentence",
    "explain",
    "generate_exercise",
    "simplify_text",
    "gloss",
)
RealProvider = Literal["anthropic", "openai", "google", "openrouter"]
# Fields of an environment task entry that only make sense for the provider they were set for.
_PROVIDER_SPECIFIC = ("effort", "params", "structured_output")


class OverrideIn(BaseModel):
    provider: RealProvider
    model: str
    structured_output: Literal["native", "json"] | None = None

    @field_validator("model")
    @classmethod
    def _model(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 200:
            raise ValueError("model must be 1 to 200 characters")
        return value

    @model_validator(mode="after")
    def _structured_output_not_for_anthropic(self) -> Self:
        if self.provider == "anthropic" and self.structured_output is not None:
            raise ValueError("structured_output is not supported for provider 'anthropic'")
        return self


def check_task_names(names: Any) -> None:
    unknown = sorted(set(names) - set(DEFAULT_TASKS))
    if unknown:
        raise ValueError(f"unknown task(s): {', '.join(unknown)}")


def apply_overrides(settings: Settings, overrides: dict[str, OverrideIn]) -> Settings:
    """A copy of `settings` whose `llm_tasks` carry the overrides (the input is not modified)."""
    tasks = {name: dict(entry) for name, entry in settings.llm_tasks.items()}
    for task, override in overrides.items():
        entry = tasks.get(task, {})
        env_provider = entry.get("provider") or settings.llm_provider
        if override.provider != env_provider:
            entry = {k: v for k, v in entry.items() if k not in _PROVIDER_SPECIFIC}
        entry = {**entry, "provider": override.provider, "model": override.model}
        if override.structured_output is not None:
            entry["structured_output"] = override.structured_output
        tasks[task] = entry
    return settings.model_copy(update={"llm_tasks": tasks})


def load_overrides(session: Session) -> dict[str, OverrideIn]:
    """The stored overrides; invalid entries are skipped with a warning."""
    row = session.get(AppSetting, SETTINGS_KEY)
    raw = row.value if row is not None else None
    if not isinstance(raw, dict):
        return {}
    result: dict[str, OverrideIn] = {}
    for task in TASK_ORDER:
        if task not in raw:
            continue
        try:
            result[task] = OverrideIn.model_validate(raw[task])
        except ValidationError:
            logger.warning("ignoring invalid stored LLM override for task %r", task)
    for task in set(raw) - set(TASK_ORDER):
        logger.warning("ignoring stored LLM override for unknown task %r", task)
    return result


def save_overrides(session: Session, overrides: dict[str, OverrideIn], now: datetime) -> None:
    value = {task: o.model_dump(exclude_none=True) for task, o in overrides.items()}
    row = session.get(AppSetting, SETTINGS_KEY)
    if row is None:
        session.add(AppSetting(key=SETTINGS_KEY, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
    session.commit()
