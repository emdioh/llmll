"""Per-task LLM configuration (design: docs/design/M2.md §1.2, M6-providers.md §2)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-opus-5-5"

Provider = Literal["anthropic", "openai", "google", "openrouter", "fake"]
PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "google", "openrouter", "fake")


class TaskConfig(BaseModel):
    # `provider` stays None until the factory resolves it (task setting -> global default). The
    # built-in model default is Anthropic's; the factory replaces it for other providers.
    provider: Provider | None = None
    model: str = DEFAULT_MODEL
    effort: str = "medium"  # Anthropic only
    max_tokens: int = 4000
    # Provider-specific request parameters, passed through unchanged.
    params: dict[str, Any] = Field(default_factory=dict)
    # `native`: schema-constrained output of the provider. `json`: JSON asked for in the prompt,
    # validated with Pydantic, one retry with the validation error (OpenAI-compatible and Gemini).
    structured_output: Literal["native", "json"] = "native"


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
        tasks[name] = TaskConfig.model_validate({**base.model_dump(), **fields})
    return tasks


def route_label(cfg: TaskConfig, default_provider: str) -> str:
    """`provider/model`, as reported by the health endpoint."""
    return f"{cfg.provider or default_provider}/{cfg.model}"
