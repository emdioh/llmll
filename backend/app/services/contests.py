"""Contests: resolvers and the application of a resolution (design: M4 §2, A§6.4)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.projection import REVIEW_KINDS
from app.domain.reconcile import Evaluation, ItemOutcome
from app.llm.types import GradeError
from app.nlp.types import LTMatch
from app.services.common import SessionError
from app.services.corpus import item_label
from app.services.learner import projection_config, settings_of
from app.store.events import replace_events
from app.store.models import (
    Attempt,
    Contest,
    Exercise,
    Item,
    Learner,
    LearningEvent,
    RemediationItem,
)
from app.store.models import Evaluation as EvaluationRow

FLASHCARD_GRADER = "flashcards.v1"
FLASHCARD_TYPES = ("flashcard_recognition", "flashcard_production")
OVERALL_OF_OUTCOME = {"correct": "correct", "assisted": "minor_errors", "error": "major_errors"}
ACCEPTED_FEEDBACK = "Ok, conteggiato come corretto."

Verdict = Literal["accepted", "rejected", "partial"]


@dataclass
class Resolution:
    verdict: Verdict
    replacement: Evaluation | None  # the new evaluation, when the contest changes anything
    rationale: str


class ContestResolver(Protocol):
    name: str

    def resolve(self, contest: Contest, evaluation: Evaluation) -> Resolution: ...


def _overall(items: tuple[ItemOutcome, ...], errors: tuple[GradeError, ...]) -> str:
    if errors:
        return "major_errors" if any(e.severity == "major" for e in errors) else "minor_errors"
    if any(i.outcome == "error" for i in items):
        return "major_errors"
    if any(i.outcome == "assisted" for i in items):
        return "minor_errors"
    return "correct"


class AcceptAllResolver:
    """v1 resolver: the learner is always right. Contested errors disappear and the contested
    items count as correct uses (confidence 1.0). No item ids = the whole evaluation."""

    name = "accept_all"

    def resolve(self, contest: Contest, evaluation: Evaluation) -> Resolution:
        whole = not contest.item_ids
        scope = {i.item_id for i in evaluation.items} if whole else set(contest.item_ids)
        items = tuple(
            ItemOutcome(i.item_id, "correct", 1.0, ()) if i.item_id in scope else i
            for i in evaluation.items
        )
        errors = tuple(e for e in evaluation.errors if not (whole or e.item_id in scope))
        replacement = Evaluation(
            overall=_overall(items, errors),
            items=items,
            errors=errors,
            unmatched_lt=() if whole else evaluation.unmatched_lt,
            notes=(*evaluation.notes, "contest accepted"),
            lt_available=evaluation.lt_available,
            reconcile_version=evaluation.reconcile_version,
        )
        return Resolution("accepted", replacement, "Accepted without review (accept_all resolver).")


RESOLVERS: dict[str, Callable[[], ContestResolver]] = {"accept_all": AcceptAllResolver}


def register_resolver(name: str, factory: Callable[[], ContestResolver]) -> None:
    """Plug in another resolver (stronger-model re-grade, native review queue...)."""
    RESOLVERS[name] = factory


def build_resolver(name: str) -> ContestResolver:
    try:
        return RESOLVERS[name]()
    except KeyError:
        raise ValueError(
            f"unknown contest resolver {name!r}; known: {', '.join(sorted(RESOLVERS))}"
        ) from None


# --- evaluations -------------------------------------------------------------------------------


def evaluation_from_row(row: EvaluationRow) -> Evaluation:
    r = row.result
    return Evaluation(
        overall=r["overall"],
        items=tuple(
            ItemOutcome(
                i["item_id"], i["outcome"], i["confidence"], tuple(i.get("diagnostic_tags", ()))
            )
            for i in r.get("items", [])
        ),
        errors=tuple(GradeError.model_validate(e) for e in r.get("errors", [])),
        unmatched_lt=tuple(LTMatch.model_validate(m) for m in r.get("unmatched_lt", [])),
        notes=tuple(r.get("notes", ())),
        lt_available=r.get("lt_available", False),
        reconcile_version=r.get("reconcile_version", ""),
    )


def flashcard_result(
    item_id: str,
    outcome: str,
    tags: list[str],
    corrected: str,
    feedback: str,
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "overall": OVERALL_OF_OUTCOME[outcome],
        "items": [
            {
                "item_id": item_id,
                "outcome": outcome,
                "confidence": confidence,
                "diagnostic_tags": list(tags),
            }
        ],
        "errors": [],
        "unmatched_lt": [],
        "notes": [],
        "lt_available": False,
        "reconcile_version": FLASHCARD_GRADER,
        "corrected_sentence": corrected,
        "feedback": feedback,
    }


def create_flashcard_evaluation(
    db: Session,
    attempt: Attempt,
    item_id: str,
    outcome: str,
    tags: list[str],
    corrected: str,
    feedback: str,
    now: datetime,
) -> EvaluationRow:
    """The synthetic evaluation of a flashcard attempt (grader `flashcards.v1`)."""
    row = EvaluationRow(
        attempt_id=attempt.id,
        llm_call_id=None,
        lt_matches=None,
        result=flashcard_result(item_id, outcome, tags, corrected, feedback),
        grader_version=FLASHCARD_GRADER,
        created_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _latest_evaluation(db: Session, attempt_id: int) -> EvaluationRow | None:
    return db.scalar(
        select(EvaluationRow)
        .where(EvaluationRow.attempt_id == attempt_id)
        .order_by(EvaluationRow.id.desc())
        .limit(1)
    )


def _load_attempt(db: Session, learner: Learner, attempt_id: int) -> tuple[Attempt, Exercise]:
    attempt = db.get(Attempt, attempt_id)
    exercise = db.get(Exercise, attempt.exercise_id) if attempt is not None else None
    if attempt is None or exercise is None or exercise.learner_id != learner.id:
        raise SessionError(404, "Attempt not found")
    return attempt, exercise


def ensure_flashcard_evaluation(
    db: Session, learner: Learner, attempt_id: int, now: datetime
) -> EvaluationRow:
    """The evaluation of an attempt; flashcard attempts recorded before M4 get a synthetic one
    on demand (their events carry no `evaluation_id`; they are found through `attempt_id`)."""
    attempt, exercise = _load_attempt(db, learner, attempt_id)
    existing = _latest_evaluation(db, attempt.id)
    if existing is not None:
        return existing
    if exercise.type not in FLASHCARD_TYPES:
        raise SessionError(409, "This attempt has no evaluation and cannot be contested")
    events = db.scalars(
        select(LearningEvent).where(
            LearningEvent.attempt_id == attempt.id, LearningEvent.kind == "review"
        )
    ).all()
    tags = list(dict.fromkeys(t for e in events for t in e.diagnostic_tags or ()))
    solution = exercise.solution
    return create_flashcard_evaluation(
        db,
        attempt,
        exercise.targets[0]["item_id"],
        attempt.outcome,
        tags,
        solution.get("text", ""),
        "",
        now,
    )


# --- contesting --------------------------------------------------------------------------------


@dataclass
class ContestOutcome:
    contest: Contest
    evaluation_id: int  # the evaluation that is current now
    items: list[dict[str, Any]]  # {item_id, label, previous, outcome}


def _events_of(db: Session, evaluation: EvaluationRow) -> list[LearningEvent]:
    """Non-voided events of an evaluation. Flashcard events recorded before M4 have no
    `evaluation_id`; they belong to the evaluation through their attempt."""
    return list(
        db.scalars(
            select(LearningEvent).where(
                LearningEvent.voided_by.is_(None),
                (LearningEvent.evaluation_id == evaluation.id)
                | (
                    LearningEvent.evaluation_id.is_(None)
                    & (LearningEvent.attempt_id == evaluation.attempt_id)
                ),
            )
        )
    )


def contest_evaluation(
    db: Session,
    learner: Learner,
    evaluation: EvaluationRow,
    item_ids: list[str],
    reason: str | None,
    resolver: ContestResolver,
    now: datetime,
) -> ContestOutcome:
    """Contest an evaluation and apply the resolution in one transaction."""
    attempt, exercise = _load_attempt(db, learner, evaluation.attempt_id)
    if db.scalar(select(EvaluationRow.id).where(EvaluationRow.supersedes == evaluation.id)):
        raise SessionError(409, "This evaluation was already superseded by a contest")
    old = evaluation_from_row(evaluation)
    known = {i.item_id for i in old.items}
    unknown = [i for i in item_ids if i not in known]
    if unknown:
        raise SessionError(422, f"Items not part of this evaluation: {', '.join(unknown)}")

    contest = Contest(
        evaluation_id=evaluation.id,
        item_ids=list(dict.fromkeys(item_ids)),
        reason=reason,
        status="open",
        resolver=getattr(resolver, "name", type(resolver).__name__),
        created_at=now,
    )
    db.add(contest)
    db.flush()
    resolution = resolver.resolve(contest, old)
    contest.verdict = resolution.verdict
    contest.rationale = resolution.rationale
    previous = {i.item_id: i.outcome for i in old.items}

    if resolution.verdict == "rejected" or resolution.replacement is None:
        if resolution.verdict != "rejected":
            raise SessionError(500, "Resolver accepted the contest without a replacement")
        contest.status = "resolved"
        contest.resolved_at = now
        db.commit()
        current = evaluation.id
        outcomes = old.items
    else:
        current = _apply(db, learner, evaluation, attempt, resolution.replacement, contest, now)
        outcomes = resolution.replacement.items
    labels = {
        i.id: item_label(i)
        for i in db.scalars(select(Item).where(Item.id.in_([o.item_id for o in outcomes])))
    }
    return ContestOutcome(
        contest,
        current,
        [
            {
                "item_id": o.item_id,
                "label": labels.get(o.item_id, o.item_id),
                "previous": previous.get(o.item_id),
                "outcome": o.outcome,
            }
            for o in outcomes
        ],
    )


def _apply(
    db: Session,
    learner: Learner,
    evaluation: EvaluationRow,
    attempt: Attempt,
    replacement: Evaluation,
    contest: Contest,
    now: datetime,
) -> int:
    cfg = projection_config(settings_of(learner))
    result = replacement.to_dict()
    result["corrected_sentence"] = evaluation.result.get("corrected_sentence", "")
    result["feedback"] = evaluation.result.get("feedback", "")
    if replacement.overall == "correct":
        result["corrected_sentence"] = attempt.answer.get("text") or result["corrected_sentence"]
        result["feedback"] = ACCEPTED_FEEDBACK
    row = EvaluationRow(
        attempt_id=evaluation.attempt_id,
        llm_call_id=None,
        lt_matches=evaluation.lt_matches,
        result=result,
        grader_version=f"{evaluation.grader_version}|contest.{contest.resolver}",
        supersedes=evaluation.id,
        created_at=now,
    )
    db.add(row)
    db.flush()

    by_item = {i.item_id: i for i in replacement.items}

    def changes(event: LearningEvent) -> dict[str, Any]:
        outcome = by_item.get(event.item_id)
        if outcome is None or event.kind not in REVIEW_KINDS or event.outcome is None:
            return {}
        return {
            "outcome": outcome.outcome,
            "confidence": outcome.confidence,
            "diagnostic_tags": list(outcome.diagnostic_tags),
        }

    replace_events(db, cfg, _events_of(db, evaluation), row.id, changes)

    # Remediation entries raised by the old evaluation: dropped when the item no longer counts
    # as an error, otherwise they point at the replacement.
    for entry in db.scalars(
        select(RemediationItem).where(
            RemediationItem.learner_id == learner.id,
            RemediationItem.evaluation_id == evaluation.id,
            RemediationItem.consumed_at.is_(None),
        )
    ):
        outcome = by_item.get(entry.item_id)
        if outcome is None or outcome.outcome != "error":
            db.delete(entry)
        else:
            entry.evaluation_id = row.id
            entry.diagnostic_tags = list(outcome.diagnostic_tags)

    contest.status = "resolved"
    contest.resolved_at = now
    contest.replacement_evaluation_id = row.id
    db.commit()
    return row.id
