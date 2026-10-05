"""Explanations on errors and on demand (design: docs/design/M2.md §4)."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.calls import last_call_id
from app.llm.client import LLMClient, LLMError, LLMUnavailable
from app.llm.types import ErrorContext, ExplainRequest, KnownGrammar
from app.services.common import SessionError
from app.services.corpus import item_label
from app.services.production import item_context
from app.store.models import (
    Attempt,
    Evaluation,
    Exercise,
    Item,
    Learner,
    LearnerItem,
    StoredExplanation,
)

KNOWN_STATUSES = ("introduced", "presumed_known")


def _known_grammar(db: Session, learner: Learner) -> list[KnownGrammar]:
    rows = db.execute(
        select(Item)
        .join(LearnerItem, LearnerItem.item_id == Item.id)
        .where(
            LearnerItem.learner_id == learner.id,
            LearnerItem.status.in_(KNOWN_STATUSES),
            Item.kind == "grammar",
            ~Item.suspended,
        )
        .order_by(Item.id)
    ).scalars()
    return [KnownGrammar(item_id=i.id, title=item_label(i)) for i in rows]


def _call(llm: LLMClient, request: ExplainRequest) -> Any:
    try:
        return llm.explain(request)
    except LLMUnavailable as exc:
        raise SessionError(503, f"Explanations are temporarily unavailable: {exc}") from None
    except LLMError as exc:
        raise SessionError(502, f"Explanation failed: {exc}") from None


def _out(markdown: str, examples: list[dict[str, str]], cached: bool) -> dict[str, Any]:
    return {"markdown": markdown, "examples": examples, "cached": cached}


def explain_evaluation(
    db: Session,
    learner: Learner,
    evaluation_id: int,
    item_id: str,
    llm: LLMClient,
    now: datetime,
) -> dict[str, Any]:
    evaluation = db.get(Evaluation, evaluation_id)
    attempt = db.get(Attempt, evaluation.attempt_id) if evaluation else None
    exercise = db.get(Exercise, attempt.exercise_id) if attempt else None
    if (
        evaluation is None
        or attempt is None
        or exercise is None
        or exercise.learner_id != learner.id
    ):
        raise SessionError(404, "Evaluation not found")
    result = evaluation.result
    related = (
        {i["item_id"] for i in result.get("items", [])}
        | {e["item_id"] for e in result.get("errors", []) if e.get("item_id")}
        | {t["item_id"] for t in exercise.targets}
    )
    item = db.get(Item, item_id)
    if item is None or item_id not in related:
        raise SessionError(404, "Item not part of this evaluation")

    cached = db.scalar(
        select(StoredExplanation).where(
            StoredExplanation.evaluation_id == evaluation_id, StoredExplanation.item_id == item_id
        )
    )
    if cached is not None:
        return _out(cached.markdown, cached.examples, True)

    error = next((e for e in result.get("errors", []) if e.get("item_id") == item_id), None)
    context = None
    if error is not None:
        context = ErrorContext(
            answer=attempt.answer.get("text", ""),
            original=error["original"],
            correction=error["correction"],
            diagnostic_tags=error.get("diagnostic_tags", []),
        )
    explanation = _call(
        llm,
        ExplainRequest(
            item=item_context(item),
            error=context,
            level=learner.level,
            explanation_language=learner.explanation_language,
            known_grammar=_known_grammar(db, learner),
        ),
    )
    examples = [e.model_dump() for e in explanation.examples]
    db.add(
        StoredExplanation(
            evaluation_id=evaluation_id,
            item_id=item_id,
            llm_call_id=last_call_id(),
            markdown=explanation.markdown,
            examples=examples,
            created_at=now,
        )
    )
    db.commit()
    return _out(explanation.markdown, examples, False)


def explain_grammar(
    db: Session, learner: Learner, grammar_id: str, question: str, llm: LLMClient
) -> dict[str, Any]:
    item = db.get(Item, grammar_id)
    if item is None or item.kind != "grammar":
        raise SessionError(404, "Grammar point not found")
    explanation = _call(
        llm,
        ExplainRequest(
            item=item_context(item),
            question=question,
            level=learner.level,
            explanation_language=learner.explanation_language,
            known_grammar=_known_grammar(db, learner),
        ),
    )
    return _out(explanation.markdown, [e.model_dump() for e in explanation.examples], False)
