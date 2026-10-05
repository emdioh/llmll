"""Production exercises: planning, generation (prepare) and grading (design: M2 §2, §3)."""

import logging
import re
import threading
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from fsrs import Card
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.curriculum.schema import CEFR_LEVELS
from app.domain.config import ReconcileConfig
from app.domain.production import Candidate, PlannedExercise, plan_production
from app.domain.reconcile import Evaluation, ItemOutcome, reconcile
from app.domain.scheduling import retrievability
from app.llm.calls import last_call_id
from app.llm.client import LLMClient, LLMError, LLMUnavailable
from app.llm.types import (
    ExamplePair,
    ExerciseRequest,
    GeneratedExercise,
    GlossEntry,
    GradeRequest,
    ItemContext,
    TargetContext,
    VocabEntry,
)
from app.nlp.languagetool import LanguageToolClient
from app.services.common import BuiltCard, SessionError
from app.services.corpus import item_label, item_translation_it
from app.services.learner import projection_config, settings_of
from app.store.events import append_event, append_event_with_decision
from app.store.models import (
    Attempt,
    Exercise,
    Item,
    ItemMemory,
    ItemPrerequisite,
    Learner,
    LearnerItem,
    LLMCall,
    RemediationItem,
)
from app.store.models import (
    Evaluation as EvaluationRow,
)

logger = logging.getLogger(__name__)

KNOWN_VOCAB_LIMIT = 150
MAX_GENERATION_ATTEMPTS = 2
KNOWN_STATUSES = ("introduced", "presumed_known")
TYPE_WEIGHT = {
    "translation": "translation",
    "guided": "guided",
    "transform": "guided",
    "summary": "free",
}
RECONCILE_CFG = ReconcileConfig()
_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


# --- item contexts -----------------------------------------------------------------------------


def flat_tags(item: Item) -> list[str]:
    tags = (item.payload.get("diagnostic_tags") or {}) if item.kind == "grammar" else {}
    return sorted({value for values in tags.values() for value in values})


def item_context(item: Item) -> ItemContext:
    p = item.payload
    example = p.get("example")
    base: dict[str, Any] = {
        "item_id": item.id,
        "kind": item.kind,
        "label": item_label(item),
        "level": item.cefr_level,
        "translation_it": item_translation_it(item),
    }
    if item.kind == "lemma":
        base.update(
            translation_en=p["translations"]["en"],
            pos=p["pos"],
            gender=p.get("gender"),
            plural=p.get("plural"),
        )
    elif item.kind == "construction":
        base.update(translation_en=p["translations"]["en"], pattern=p["pattern"])
    else:
        base.update(
            translation_en=p["title_en"],
            reference_it=p["reference_it"],
            diagnostic_tags=p.get("diagnostic_tags", {}),
        )
        example = (p.get("examples") or [None])[0]
    if example:
        base["example"] = ExamplePair(**example)
    return ItemContext(**base)


def first_section(reference: str) -> str:
    """The first `## ` section of a reference text, without its heading."""
    sections = re.split(r"^## .*$", reference, flags=re.MULTILINE)
    body = [s.strip() for s in sections if s.strip()]
    return body[0] if body else reference.strip()


# --- planning -------------------------------------------------------------------------------


def _grammar_intro_card(
    learner_id: int, session_id: str, item: Item, now: datetime
) -> tuple[Exercise, BuiltCard]:
    p = item.payload
    if item.kind == "grammar":
        prompt = {
            "title": p["title_it"],
            "reference_it": first_section(p["reference_it"]),
            "examples": p["examples"],
        }
    else:
        prompt = {
            "title": p["pattern"],
            "reference_it": p["translations"]["it"],
            "examples": [p["example"]],
        }
    exercise = Exercise(
        id=uuid.uuid4().hex,
        learner_id=learner_id,
        type="grammar_intro",
        prompt=prompt,
        solution={},
        targets=[{"item_id": item.id, "facet": "production", "weight": 0.0, "new": True}],
        generator="grammar_intro.v1",
        created_at=now,
        session_id=session_id,
        status="ready",
    )
    return exercise, BuiltCard(exercise.id, "grammar_intro", item.id, prompt)


def _eligible_new_grammar(db: Session, learner: Learner) -> list[Item]:
    """Unseen grammar points and constructions up to the learner's level, prerequisites met."""
    rank = CEFR_LEVELS.index(learner.level)
    rows = db.execute(
        select(Item, LearnerItem.status)
        .join(LearnerItem, LearnerItem.item_id == Item.id)
        .where(
            LearnerItem.learner_id == learner.id,
            Item.kind.in_(("grammar", "construction")),
            ~Item.suspended,
        )
    ).all()
    all_status = dict(
        db.execute(
            select(LearnerItem.item_id, LearnerItem.status).where(
                LearnerItem.learner_id == learner.id
            )
        ).all()
    )
    prereqs: defaultdict[str, list[str]] = defaultdict(list)
    for item_id, required in db.execute(
        select(ItemPrerequisite.item_id, ItemPrerequisite.requires_item_id)
    ):
        prereqs[item_id].append(required)
    eligible = [
        item
        for item, status in rows
        if status in ("unseen", "candidate")
        and CEFR_LEVELS.index(item.cefr_level) <= rank
        and all(all_status.get(r) in KNOWN_STATUSES for r in prereqs.get(item.id, []))
    ]
    eligible.sort(key=lambda i: (CEFR_LEVELS.index(i.cefr_level), i.kind != "grammar", i.id))
    return eligible


def plan_production_slots(
    db: Session,
    learner: Learner,
    session_id: str,
    cfg: Any,
    now: datetime,
    *,
    slots: int,
    lemma_candidates: list[Item],
    lemma_limit: int,
    grammar_budget: int,
) -> list[tuple[Exercise, BuiltCard]]:
    """Create the pending production exercises (and grammar intros) of a session."""
    memories = {
        m.item_id: m
        for m in db.scalars(
            select(ItemMemory).where(
                ItemMemory.learner_id == learner.id, ItemMemory.facet == "production"
            )
        )
    }
    items_by_id: dict[str, Item] = {}

    remediation_rows = db.execute(
        select(RemediationItem, Item)
        .join(Item, Item.id == RemediationItem.item_id)
        .where(
            RemediationItem.learner_id == learner.id,
            RemediationItem.consumed_at.is_(None),
            ~Item.suspended,
        )
        .order_by(RemediationItem.created_at, RemediationItem.id)
    ).all()
    remediation: list[Candidate] = []
    tags_of: dict[str, list[str]] = {}
    rows_of: defaultdict[str, list[RemediationItem]] = defaultdict(list)
    for row, item in remediation_rows:
        rows_of[item.id].append(row)
        tags_of.setdefault(item.id, [])
        tags_of[item.id].extend(t for t in row.diagnostic_tags if t not in tags_of[item.id])
        if item.id not in items_by_id:
            items_by_id[item.id] = item
            mem = memories.get(item.id)
            remediation.append(Candidate(item.id, item.kind, mem.mastery if mem else None))

    due_rows = db.execute(
        select(ItemMemory, Item)
        .join(Item, Item.id == ItemMemory.item_id)
        .where(
            ItemMemory.learner_id == learner.id,
            ItemMemory.facet == "production",
            ItemMemory.due.is_not(None),
            ItemMemory.due <= now,
            Item.kind.in_(("grammar", "construction")),
            ~Item.suspended,
        )
    ).all()
    due: list[Candidate] = []
    for mem, item in due_rows:
        items_by_id.setdefault(item.id, item)
        card = Card.from_dict(mem.fsrs_card) if mem.fsrs_card else None
        r = retrievability(card, now, cfg.desired_retention) if card is not None else None
        due.append(Candidate(item.id, item.kind, mem.mastery, r))

    new_grammar_items = _eligible_new_grammar(db, learner)
    for item in new_grammar_items:
        items_by_id.setdefault(item.id, item)
    for item in lemma_candidates[:lemma_limit]:
        items_by_id.setdefault(item.id, item)

    plan = plan_production(
        slots,
        remediation,
        due,
        [Candidate(i.id, i.kind) for i in new_grammar_items],
        [Candidate(i.id, "lemma") for i in lemma_candidates[:lemma_limit]],
        grammar_budget,
        lemma_limit,
    )
    result: list[tuple[Exercise, BuiltCard]] = []
    for planned in plan:
        result.extend(_build_exercise(learner.id, session_id, planned, items_by_id, tags_of, now))
        for target in planned.targets:
            for row in rows_of.get(target.item_id, []):
                row.consumed_at = now
    return result


def _build_exercise(
    learner_id: int,
    session_id: str,
    planned: PlannedExercise,
    items: dict[str, Item],
    tags_of: dict[str, list[str]],
    now: datetime,
) -> list[tuple[Exercise, BuiltCard]]:
    cards: list[tuple[Exercise, BuiltCard]] = []
    for target in planned.targets:
        if target.new and target.kind != "lemma":
            cards.append(_grammar_intro_card(learner_id, session_id, items[target.item_id], now))
    targets = [
        {
            "item_id": t.item_id,
            "facet": "production",
            "weight": t.weight,
            "new": t.new,
            "role": t.role,
            "kind": t.kind,
            "focus_tags": tags_of.get(t.item_id, []),
        }
        for t in planned.targets
    ]
    exercise = Exercise(
        id=uuid.uuid4().hex,
        learner_id=learner_id,
        type="production",
        prompt={"subtype": planned.subtype},
        solution={},
        targets=targets,
        generator="pending",
        created_at=now,
        session_id=session_id,
        status="pending",
    )
    cards.append((exercise, production_card(exercise)))
    return cards


def production_card(exercise: Exercise) -> BuiltCard:
    """The learner-facing payload of a production exercise (never the reference solutions)."""
    p = exercise.prompt
    ready = exercise.status in ("ready", "answered")
    return BuiltCard(
        exercise_id=exercise.id,
        type="production",
        item_id=exercise.targets[0]["item_id"] if exercise.targets else None,
        prompt=p.get("prompt") if ready else None,
        status=exercise.status,
        subtype=p.get("subtype"),
        instructions=p.get("instructions") if ready else None,
        glossary=p.get("glossary", []) if ready else [],
        item_ids=[t["item_id"] for t in exercise.targets],
    )


# --- generation -----------------------------------------------------------------------------


def known_vocabulary(db: Session, learner: Learner, exclude: set[str]) -> list[VocabEntry]:
    rows = db.execute(
        select(Item, LearnerItem.status)
        .join(LearnerItem, LearnerItem.item_id == Item.id)
        .where(
            LearnerItem.learner_id == learner.id,
            Item.kind == "lemma",
            ~Item.suspended,
            LearnerItem.status.in_(KNOWN_STATUSES),
        )
    ).all()
    mastery: dict[str, float] = {}
    for item_id, value in db.execute(
        select(ItemMemory.item_id, ItemMemory.mastery).where(ItemMemory.learner_id == learner.id)
    ):
        mastery[item_id] = max(value, mastery.get(item_id, 0.0))
    known = [item for item, _ in rows if item.id not in exclude]
    known.sort(key=lambda i: (-mastery.get(i.id, 0.5), -(i.frequency_zipf or 0.0), i.id))
    chosen = known[:KNOWN_VOCAB_LIMIT]
    if len(chosen) < KNOWN_VOCAB_LIMIT:
        rank = CEFR_LEVELS.index(learner.level)
        taken = {i.id for i in chosen} | exclude
        lower = db.scalars(
            select(Item).where(Item.kind == "lemma", ~Item.suspended).order_by(Item.id)
        ).all()
        extra = [i for i in lower if i.id not in taken and CEFR_LEVELS.index(i.cefr_level) < rank]
        extra.sort(key=lambda i: (-(i.frequency_zipf or 0.0), i.id))
        chosen += extra[: KNOWN_VOCAB_LIMIT - len(chosen)]
    return [
        VocabEntry(item_id=i.id, de=item_label(i), translation=item_translation_it(i) or "")
        for i in chosen
    ]


def _target_contexts(db: Session, exercise: Exercise) -> list[TargetContext]:
    contexts = []
    for t in exercise.targets:
        item = db.get(Item, t["item_id"])
        if item is None:
            continue
        contexts.append(
            TargetContext(
                item=item_context(item),
                is_new=bool(t.get("new")),
                weight=t["weight"],
                role=t.get("role", "primary"),
                focus_tags=t.get("focus_tags", []),
            )
        )
    return contexts


def _validate_generated(
    gen: GeneratedExercise, exercise: Exercise
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return `(targets, problems)`; unknown target ids are dropped."""
    problems: list[str] = []
    planned = {t["item_id"]: t for t in exercise.targets}
    if not gen.prompt.strip():
        problems.append("empty prompt")
    if not [s for s in gen.reference_solutions if s.strip()]:
        problems.append("no reference solution")
    glossed = {g.item_id for g in gen.glossary}
    for t in exercise.targets:
        if t.get("new") and t.get("kind") == "lemma" and t["item_id"] not in glossed:
            problems.append(f"new lemma {t['item_id']} missing from the glossary")
    returned = {t.item_id: t.weight for t in gen.targets if t.item_id in planned}
    if exercise.targets and exercise.targets[0]["item_id"] not in returned:
        problems.append("primary target missing from the targets")
    targets = [
        {**planned[i], "weight": w if 0 < w <= 2 else planned[i]["weight"]}
        for i, w in returned.items()
    ]
    return targets, problems


def prepare_exercise(
    db: Session,
    learner: Learner,
    exercise_id: str,
    llm: LLMClient,
    now: datetime,
) -> tuple[Exercise, list[BuiltCard]]:
    """Generate a pending production exercise. Idempotent; returns `(exercise, fallback cards)`."""
    with _locks[exercise_id]:
        exercise = db.get(Exercise, exercise_id)
        if exercise is None or exercise.learner_id != learner.id or exercise.type != "production":
            raise SessionError(404, "Production exercise not found")
        db.refresh(exercise)
        if exercise.status == "failed":
            return exercise, _fallback_cards(db, exercise)
        if exercise.status != "pending":
            return exercise, []

        contexts = _target_contexts(db, exercise)
        target_ids = {c.item.item_id for c in contexts}
        request = ExerciseRequest(
            exercise_type=exercise.prompt["subtype"],
            level=learner.level,
            explanation_language=learner.explanation_language,
            targets=contexts,
            known_vocabulary=known_vocabulary(db, learner, target_ids),
        )
        problems: list[str] = []
        for _attempt in range(MAX_GENERATION_ATTEMPTS):
            try:
                generated = llm.generate_exercise(request)
            except LLMError as exc:
                logger.warning("exercise generation failed: %s", exc)
                problems = [str(exc)]
                break
            call_id = last_call_id()
            targets, problems = _validate_generated(generated, exercise)
            if not problems:
                _store_generated(db, exercise, generated, targets, call_id)
                db.commit()
                return exercise, []
            logger.warning("generated exercise rejected: %s", problems)
        exercise.status = "failed"
        exercise.solution = {"errors": problems}
        fallback = _make_fallback(db, learner, exercise, now)
        exercise.solution = {
            "errors": problems,
            "fallback_exercise_ids": [e.id for e, _ in fallback],
        }
        db.commit()
        return exercise, [card for _, card in fallback]


def _store_generated(
    db: Session,
    exercise: Exercise,
    gen: GeneratedExercise,
    targets: list[dict[str, Any]],
    call_id: int | None,
) -> None:
    call = db.get(LLMCall, call_id) if call_id else None
    exercise.prompt = {
        "subtype": exercise.prompt["subtype"],
        "instructions": gen.instructions,
        "prompt": gen.prompt,
        "glossary": [g.model_dump() for g in gen.glossary if g.de.strip()],
    }
    exercise.solution = {
        "reference_solutions": [s for s in gen.reference_solutions if s.strip()],
        "generation_call_id": call_id,
    }
    exercise.targets = targets
    exercise.generator = f"generate_exercise.{call.prompt_version}" if call else "generate_exercise"
    exercise.status = "ready"


def _make_fallback(
    db: Session, learner: Learner, exercise: Exercise, now: datetime
) -> list[tuple[Exercise, BuiltCard]]:
    from app.services.sessions import _intro_card

    cards = []
    for t in exercise.targets:
        if t.get("new") and t.get("kind") == "lemma":
            item = db.get(Item, t["item_id"])
            if item is not None:
                cards.append(_intro_card(learner.id, exercise.session_id, item, now))
    db.add_all(e for e, _ in cards)
    return cards


def _fallback_cards(db: Session, exercise: Exercise) -> list[BuiltCard]:
    cards = []
    for ex_id in exercise.solution.get("fallback_exercise_ids", []):
        ex = db.get(Exercise, ex_id)
        if ex is not None:
            cards.append(BuiltCard(ex.id, ex.type, ex.targets[0]["item_id"], ex.prompt))
    return cards


# --- grading ----------------------------------------------------------------------------------


def _attempt_outcome(overall: str, used_hint: bool) -> str:
    if overall == "correct":
        return "assisted" if used_hint else "correct"
    if overall == "minor_errors":
        return "assisted"
    return "error"


def submit_production_answer(
    db: Session,
    learner: Learner,
    exercise: Exercise,
    answer: dict[str, Any],
    used_hint: bool,
    duration_ms: int | None,
    now: datetime,
    llm: LLMClient,
    languagetool: LanguageToolClient | None,
) -> dict[str, Any]:
    cfg = projection_config(settings_of(learner))
    text = answer.get("text") or ""
    subtype = exercise.prompt["subtype"]
    contexts = _target_contexts(db, exercise)
    target_ids = [t["item_id"] for t in exercise.targets]
    allowed_tags: dict[str, list[str]] = {}
    for t in exercise.targets:
        item = db.get(Item, t["item_id"])
        if item is not None and flat_tags(item):
            allowed_tags[item.id] = flat_tags(item)

    lt_matches = None
    if languagetool is not None:
        lt_matches = languagetool.check(text) if text.strip() else []
    request = GradeRequest(
        exercise_type=subtype,
        instructions=exercise.prompt["instructions"],
        prompt=exercise.prompt["prompt"],
        glossary=[GlossEntry(**g) for g in exercise.prompt.get("glossary", [])],
        reference_solutions=exercise.solution["reference_solutions"],
        answer=text,
        targets=contexts,
        allowed_tags=allowed_tags,
        lt_matches=lt_matches,
        level=learner.level,
        explanation_language=learner.explanation_language,
    )
    try:
        grade = llm.grade_sentence(request)
    except LLMUnavailable as exc:
        raise SessionError(503, f"Grading is temporarily unavailable: {exc}") from None
    except LLMError as exc:
        raise SessionError(502, f"Grading failed: {exc}") from None
    call_id = last_call_id()
    call = db.get(LLMCall, call_id) if call_id else None

    referenced = set(target_ids) | {e.item_id for e in grade.errors if e.item_id}
    referenced |= set(grade.correct_uses)
    curriculum_ids = set(db.scalars(select(Item.id).where(Item.id.in_(referenced))))
    status_of = dict(
        db.execute(
            select(LearnerItem.item_id, LearnerItem.status).where(
                LearnerItem.learner_id == learner.id, LearnerItem.item_id.in_(curriculum_ids)
            )
        ).all()
    )
    known_ids = {i for i, s in status_of.items() if s in KNOWN_STATUSES}
    evaluation = reconcile(
        grade,
        lt_matches,
        target_ids,
        known_ids,
        allowed_tags,
        RECONCILE_CFG,
        curriculum_item_ids=curriculum_ids,
        answer=text,
        used_hint=used_hint,
    )

    attempt = Attempt(
        exercise_id=exercise.id,
        answer=answer,
        used_hint=used_hint,
        duration_ms=duration_ms,
        outcome=_attempt_outcome(evaluation.overall, used_hint),
        submitted_at=now,
    )
    db.add(attempt)
    db.flush()
    grader_version = "|".join(
        [
            call.prompt_version if call else "unknown",
            call.model if call else "unknown",
            evaluation.reconcile_version,
        ]
    )
    row = EvaluationRow(
        attempt_id=attempt.id,
        llm_call_id=call_id,
        lt_matches=[m.model_dump(mode="json") for m in lt_matches]
        if lt_matches is not None
        else None,
        result={
            **evaluation.to_dict(),
            "corrected_sentence": grade.corrected_sentence,
            "feedback": grade.feedback,
        },
        grader_version=grader_version,
        created_at=now,
    )
    db.add(row)
    db.flush()

    needs = _emit_events(db, learner, exercise, attempt, row, evaluation, status_of, cfg, now)
    db.commit()

    labels = {
        i.id: item_label(i)
        for i in db.scalars(select(Item).where(Item.id.in_([o.item_id for o in evaluation.items])))
    }
    return {
        "kind": "production",
        "outcome": evaluation.overall,
        "corrected_sentence": grade.corrected_sentence,
        "errors": [e.model_dump(mode="json") for e in evaluation.errors],
        "feedback": grade.feedback,
        "items": [
            {
                "item_id": o.item_id,
                "label": labels.get(o.item_id, o.item_id),
                "outcome": o.outcome,
                "needs_remediation": o.item_id in needs,
            }
            for o in evaluation.items
        ],
        "evaluation_id": row.id,
    }


def _emit_events(
    db: Session,
    learner: Learner,
    exercise: Exercise,
    attempt: Attempt,
    evaluation_row: EvaluationRow,
    evaluation: Evaluation,
    status_of: dict[str, str],
    cfg: Any,
    now: datetime,
) -> set[str]:
    subtype = exercise.prompt["subtype"]
    target_by_id = {t["item_id"]: t for t in exercise.targets}
    common = {
        "learner_id": learner.id,
        "ts": now,
        "exercise_id": exercise.id,
        "attempt_id": attempt.id,
        "evaluation_id": evaluation_row.id,
    }
    # Introduction first: every new target becomes known by being used in the exercise.
    for item_id, target in target_by_id.items():
        learner_item = db.get(LearnerItem, (learner.id, item_id))
        if learner_item is None or learner_item.status not in ("unseen", "candidate"):
            continue
        facets = ("recognition", "production") if target.get("kind") == "lemma" else ("production",)
        for facet in facets:
            append_event(db, cfg, item_id=item_id, facet=facet, kind="introduce", **common)
        learner_item.status = "introduced"
        learner_item.introduced_at = learner_item.introduced_at or now
        status_of[item_id] = "introduced"

    needs: set[str] = set()
    for outcome in evaluation.items:
        item_id = outcome.item_id
        target = target_by_id.get(item_id)
        learner_item = db.get(LearnerItem, (learner.id, item_id))
        was_presumed = status_of.get(item_id) == "presumed_known"
        if target is not None:
            kind = "review"
            weight = cfg.weight(TYPE_WEIGHT[subtype]) * target["weight"]
        else:
            kind = "implicit"
            weight = cfg.weight("implicit_sentence")
        _event, _row, decision = append_event_with_decision(
            db,
            cfg,
            item_id=item_id,
            facet="production",
            kind=kind,
            outcome=outcome.outcome,
            evidence_weight=weight,
            diagnostic_tags=outcome.diagnostic_tags,
            presumed_known=was_presumed,
            confidence=outcome.confidence,
            **common,
        )
        if learner_item is not None and learner_item.status != "introduced":
            learner_item.status = "introduced"
            learner_item.introduced_at = learner_item.introduced_at or now
            if was_presumed:
                append_event(
                    db,
                    cfg,
                    item_id=item_id,
                    facet="production",
                    kind="status_change",
                    presumed_known=True,
                    **common,
                )
        if decision is not None and decision.needs_remediation:
            needs.add(item_id)
            _queue_remediation(db, learner.id, item_id, outcome, evaluation_row.id, now)
    return needs


def _queue_remediation(
    db: Session,
    learner_id: int,
    item_id: str,
    outcome: ItemOutcome,
    evaluation_id: int,
    now: datetime,
) -> None:
    existing = db.scalar(
        select(RemediationItem).where(
            RemediationItem.learner_id == learner_id,
            RemediationItem.item_id == item_id,
            RemediationItem.consumed_at.is_(None),
        )
    )
    if existing is not None:
        existing.diagnostic_tags = list(
            dict.fromkeys([*existing.diagnostic_tags, *outcome.diagnostic_tags])
        )
        existing.evaluation_id = evaluation_id
        return
    db.add(
        RemediationItem(
            learner_id=learner_id,
            item_id=item_id,
            diagnostic_tags=list(outcome.diagnostic_tags),
            evaluation_id=evaluation_id,
            created_at=now,
        )
    )
