"""Per-task LLM configuration."""

from typing import Any

from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-5-5"


class TaskConfig(BaseModel):
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_tokens: int = 4000


DEFAULT_TASKS: dict[str, TaskConfig] = {
    "generate_exercise": TaskConfig(effort="medium", max_tokens=4000),
    "grade_sentence": TaskConfig(effort="high", max_tokens=4000),
    "explain": TaskConfig(effort="medium", max_tokens=3000),
    "simplify_text": TaskConfig(effort="medium", max_tokens=8000),
    "gloss": TaskConfig(effort="low", max_tokens=500),
}


def resolve_tasks(overrides: dict[str, dict[str, Any]] | None) -> dict[str, TaskConfig]:
    """Defaults merged with the `LLMLL_LLM_TASKS` override (per task, per field)."""
    tasks = {name: cfg.model_copy() for name, cfg in DEFAULT_TASKS.items()}
    for name, fields in (overrides or {}).items():
        base = tasks.get(name, TaskConfig())
        tasks[name] = base.model_copy(update=fields)
    return tasks
