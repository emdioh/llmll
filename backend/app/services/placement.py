"""Initial assessment: building the test and applying its result (design: M4 §3)."""

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.curriculum.schema import CEFR_LEVELS
from app.domain.config import PlacementConfig
from app.domain.placement import (
    SCORES,
    PlacementGrammar,
    PlacementLemma,
    PlacementResult,
    estimate_level,
    sample_placement_lemmas,
    select_placement_grammar,
)
from app.domain.production import PlannedExercise, PlannedTarget
from app.services import production
from app.services import sessions as flashcards
from app.services.common import PLACEMENT_PREFIX, BuiltCard, SessionError
from app.services.learner import projection_config, settings_of, update_learner
from app.store.models import Attempt, Exercise, Item, Learner, Placement

CFG = PlacementConfig()
USER_SOURCE = "user"


def start_placement(db: Session, learner: Learner, now: datetime) -> tuple[str, list[BuiltCard]]:
    """Create a placement: recognition cards for sampled lemmas, then guided grammar exercises
    (pending, generated through `prepare` like any production exercise)."""
    placement_id = PLACEMENT_PREFIX + uuid.uuid4().hex
    cfg = projection_config(settings_of(learner))
    items = db.scalars(select(Item).where(~Item.suspended)).all()
    by_id = {i.id: i for i in items}

    lemmas = [
        PlacementLemma(i.id, i.cefr_level, i.frequency_zipf)
        for i in items
        if i.kind == "lemma" and i.source_file != USER_SOURCE
    ]
    exercises: list[tuple[Exercise, BuiltCard]] = [
        flashcards._review_card(
            db, learner.id, placement_id, by_id[item_id], "recognition", cfg, now
        )
        for item_id in sample_placement_lemmas(lemmas, learner.level, placement_id, CFG)
    ]

    grammar_sorted = sorted(
        (i for i in items if i.kind == "grammar"),
        key=lambda i: (CEFR_LEVELS.index(i.cefr_level), i.id),
    )
    grammar = [PlacementGrammar(i.id, i.cefr_level, n) for n, i in enumerate(grammar_sorted)]
    for item_id in select_placement_grammar(grammar, learner.level, CFG):
        planned = PlannedExercise(
            "guided", (PlannedTarget(item_id, "grammar", 1.0, False, "primary"),)
        )
        exercises.extend(
            production._build_exercise(learner.id, placement_id, planned, by_id, {}, now)
        )

    db.add(
        Placement(
            id=placement_id, learner_id=learner.id, declared_level=learner.level, created_at=now
        )
    )
    db.add_all(ex for ex, _ in exercises)
    db.commit()
    return placement_id, [card for _, card in exercises]


def finish_placement(
    db: Session, learner: Learner, placement_id: str, now: datetime
) -> dict[str, Any]:
    """Estimate the level from the answers so far; update the learner level when it differs."""
    placement = db.get(Placement, placement_id)
    if placement is None or placement.learner_id != learner.id:
        raise SessionError(404, "Placement not found")
    if placement.finished_at is not None:
        raise SessionError(409, "Placement already finished")

    rows = db.execute(
        select(Attempt, Exercise)
        .join(Exercise, Exercise.id == Attempt.exercise_id)
        .where(Exercise.session_id == placement_id)
    ).all()
    levels = {
        i.id: i.cefr_level
        for i in db.scalars(
            select(Item).where(Item.id.in_({e.targets[0]["item_id"] for _, e in rows}))
        )
    }
    results = [
        PlacementResult(levels[ex.targets[0]["item_id"]], attempt.outcome)
        for attempt, ex in rows
        if ex.targets[0]["item_id"] in levels
    ]
    estimated = estimate_level(results, placement.declared_level, CFG)
    changed = estimated != placement.declared_level

    scores: defaultdict[str, list[float]] = defaultdict(list)
    for r in results:
        scores[r.level].append(SCORES[r.outcome])
    bands = {
        level: {"n": len(v), "score": sum(v) / len(v)}
        for level, v in sorted(scores.items(), key=lambda kv: CEFR_LEVELS.index(kv[0]))
    }
    placement.finished_at = now
    placement.estimated_level = estimated
    placement.changed = changed
    placement.result = {"answered": len(results), "bands": bands}
    if changed:
        # Re-applies statuses to the items that have no events yet (M1 §4.1) and commits.
        update_learner(db, learner, now, level=estimated)
    else:
        db.commit()
    return {
        "placement_id": placement_id,
        "estimated_level": estimated,
        "previous_level": placement.declared_level,
        "changed": changed,
        "answered": len(results),
        "bands": bands,
    }
