"""Grader evaluation cases: file format, loader and validation against the curriculum."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain.grader_eval import Expectation, ExpectedError
from app.llm.types import ExerciseSubtype, GlossEntry, Overall, Severity
from app.nlp.types import LTMatch

DEFAULT_CASES_DIR = Path(__file__).resolve().parents[2] / "evals" / "grader" / "cases"

Category = Literal[
    "correct_reference",  # correct, equal to a reference solution
    "correct_variant",  # correct, different from every reference solution
    "stylistic",  # acceptable stylistic alternative that must not be flagged
    "italian_error",  # typical error of Italian speakers
    "multi_error",  # several errors in one answer
    "off_task",  # answer to a different task, or empty
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseTarget(Strict):
    item_id: str
    weight: float = 1.0
    role: Literal["primary", "secondary"] = "primary"


class CaseExercise(Strict):
    type: ExerciseSubtype
    instructions: str
    prompt: str
    reference_solutions: list[str] = Field(min_length=1)
    glossary: list[GlossEntry] = Field(default_factory=list)
    targets: list[CaseTarget] = Field(min_length=1)


class CaseError(Strict):
    item_id: str | None = None
    diagnostic_tags: list[str] = Field(default_factory=list)
    severity: Severity = "major"
    span_text: str = ""  # matched by text; empty = any span


class CaseExpected(Strict):
    overall: Overall
    errors: list[CaseError] = Field(default_factory=list)
    correct_uses: list[str] = Field(default_factory=list)


class GraderCase(Strict):
    id: str
    category: Category
    level: str = "A2"
    exercise: CaseExercise
    answer: str
    expected: CaseExpected
    # Recorded LanguageTool matches, used when no LanguageTool server is reachable.
    lt_matches: list[LTMatch] | None = None
    notes: str = ""
    # Drafts exported from contests are skipped until a human removes this flag.
    draft: bool = False

    @model_validator(mode="after")
    def _check(self) -> "GraderCase":
        if self.expected.overall in ("correct", "off_task") and self.expected.errors:
            raise ValueError(f"{self.id}: overall {self.expected.overall!r} cannot list errors")
        if self.expected.overall in ("minor_errors", "major_errors") and not self.expected.errors:
            raise ValueError(f"{self.id}: overall {self.expected.overall!r} needs errors")
        if self.category in ("correct_reference", "correct_variant", "stylistic"):
            if self.expected.overall != "correct":
                raise ValueError(f"{self.id}: category {self.category!r} implies overall correct")
        folded = self.answer.casefold()
        for error in self.expected.errors:
            if error.span_text and error.span_text.casefold() not in folded:
                raise ValueError(f"{self.id}: span_text {error.span_text!r} not in the answer")
        return self

    def expectation(self) -> Expectation:
        return Expectation(
            overall=self.expected.overall,
            errors=tuple(
                ExpectedError(e.item_id, e.span_text, tuple(e.diagnostic_tags), e.severity)
                for e in self.expected.errors
            ),
            correct_uses=tuple(self.expected.correct_uses),
        )


class CaseFileError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


def load_cases(directory: Path, *, include_drafts: bool = False) -> list[GraderCase]:
    """Load every `*.yaml` of a directory (a case or a list of cases per file), sorted by file."""
    errors: list[str] = []
    cases: list[GraderCase] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: invalid YAML: {exc}")
            continue
        entries = raw if isinstance(raw, list) else [raw]
        for number, entry in enumerate(entries, 1):
            try:
                cases.append(GraderCase.model_validate(entry))
            except ValidationError as exc:
                for err in exc.errors():
                    loc = ".".join(str(p) for p in err["loc"])
                    errors.append(f"{path.name}#{number}: {loc}: {err['msg']}")
            except ValueError as exc:
                errors.append(f"{path.name}#{number}: {exc}")
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            errors.append(f"duplicate case id {case.id!r}")
        seen.add(case.id)
    if errors:
        raise CaseFileError(errors)
    return cases if include_drafts else [c for c in cases if not c.draft]


def check_against_curriculum(
    cases: list[GraderCase], tags_of: dict[str, set[str]], item_ids: set[str]
) -> list[str]:
    """Problems of the cases with respect to the curriculum (unknown ids, tags not allowed)."""
    problems: list[str] = []
    for case in cases:
        for target in case.exercise.targets:
            if target.item_id not in item_ids:
                problems.append(f"{case.id}: unknown target {target.item_id}")
        for use in case.expected.correct_uses:
            if use not in item_ids:
                problems.append(f"{case.id}: unknown correct use {use}")
        for error in case.expected.errors:
            if error.item_id is None:
                if error.diagnostic_tags:
                    problems.append(f"{case.id}: tags without an item")
                continue
            if error.item_id not in item_ids:
                problems.append(f"{case.id}: unknown error item {error.item_id}")
                continue
            targets = {t.item_id for t in case.exercise.targets}
            if error.diagnostic_tags and error.item_id not in targets:
                problems.append(f"{case.id}: tags on {error.item_id}, which is not a target")
            bad = set(error.diagnostic_tags) - tags_of.get(error.item_id, set())
            if bad:
                problems.append(f"{case.id}: tags {sorted(bad)} not allowed for {error.item_id}")
    return problems
