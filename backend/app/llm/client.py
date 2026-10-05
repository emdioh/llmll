"""The typed LLM interface. Services reach the model only through `LLMClient`."""

from typing import Protocol

from app.llm.types import (
    ExerciseRequest,
    ExplainRequest,
    Explanation,
    GeneratedExercise,
    GradeRequest,
    GradeResult,
)


class LLMError(Exception):
    """Any failure of an LLM call (API error, invalid output, truncation)."""


class LLMRefusal(LLMError):
    """The model refused (final `stop_reason == "refusal"`)."""


class LLMUnavailable(LLMError):
    """The API cannot be reached right now (connection, rate limit, 5xx)."""


class LLMClient(Protocol):
    name: str  # "anthropic" | "fake", reported by the health endpoint

    def generate_exercise(self, req: ExerciseRequest) -> GeneratedExercise: ...

    def grade_sentence(self, req: GradeRequest) -> GradeResult: ...

    def explain(self, req: ExplainRequest) -> Explanation: ...
