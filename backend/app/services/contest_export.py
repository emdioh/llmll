"""Resolved contests as draft grader evaluation cases (design: docs/design/M5.md §1)."""

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import Attempt, Contest, Exercise, Learner
from app.store.models import Evaluation as EvaluationRow


def contest_case(db: Session, contest: Contest) -> dict[str, Any] | None:
    """The draft case of a resolved contest, or `None` when it has no gradable production
    exercise behind it (flashcards). Expected = the replacement evaluation, or the original one
    when the contest was rejected."""
    original = db.get(EvaluationRow, contest.evaluation_id)
    attempt = db.get(Attempt, original.attempt_id) if original else None
    exercise = db.get(Exercise, attempt.exercise_id) if attempt else None
    if (
        original is None
        or attempt is None
        or exercise is None
        or exercise.type != "production"
        or not exercise.solution.get("reference_solutions")
    ):
        return None
    current = (
        db.get(EvaluationRow, contest.replacement_evaluation_id)
        if contest.replacement_evaluation_id
        else original
    )
    result = (current or original).result
    learner = db.get(Learner, exercise.learner_id)
    errors = [
        {
            "item_id": e.get("item_id"),
            "diagnostic_tags": e.get("diagnostic_tags", []),
            "severity": e["severity"],
            "span_text": e.get("original", ""),
        }
        for e in result.get("errors", [])
    ]
    error_items = {e["item_id"] for e in errors}
    category = (
        "correct_variant"
        if result["overall"] == "correct"
        else "multi_error"
        if len(errors) > 1
        else "italian_error"
    )
    reason = f"; learner: {contest.reason}" if contest.reason else ""
    return {
        "id": f"contest-{contest.id}",
        "draft": True,
        "category": category,
        "level": learner.level if learner else "A2",
        "exercise": {
            "type": exercise.prompt["subtype"],
            "instructions": exercise.prompt["instructions"],
            "prompt": exercise.prompt["prompt"],
            "reference_solutions": exercise.solution["reference_solutions"],
            "glossary": exercise.prompt.get("glossary", []),
            "targets": [
                {
                    "item_id": t["item_id"],
                    "weight": t.get("weight", 1.0),
                    "role": t.get("role", "primary"),
                }
                for t in exercise.targets
            ],
        },
        "answer": attempt.answer.get("text", ""),
        "expected": {
            "overall": result["overall"],
            "errors": errors,
            "correct_uses": [
                i["item_id"]
                for i in result.get("items", [])
                if i["outcome"] != "error" and i["item_id"] not in error_items
            ],
        },
        "lt_matches": original.lt_matches,
        "notes": (
            f"DRAFT from contest {contest.id} (verdict {contest.verdict}, resolver "
            f"{contest.resolver}){reason}. Review the expectation, set the category, then "
            "remove `draft: true`."
        ),
    }


def export_contests(db: Session, directory: Path) -> tuple[int, int]:
    """Write `contest-<id>.yaml` for every resolved contest; existing files are kept.

    Returns `(written, skipped)`.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for contest in db.scalars(
        select(Contest).where(Contest.status == "resolved").order_by(Contest.id)
    ):
        path = directory / f"contest-{contest.id}.yaml"
        case = None if path.exists() else contest_case(db, contest)
        if case is None:
            skipped += 1
            continue
        path.write_text(
            yaml.safe_dump(case, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8",
        )
        written += 1
    return written, skipped
