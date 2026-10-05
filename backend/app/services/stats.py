"""Learning statistics and scheduler health (design: docs/design/M5.md §3)."""

from datetime import datetime, timedelta
from typing import Any

from fsrs import Card
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.domain.retention import RetentionEvent, retention_report
from app.domain.selection import MemoryView
from app.services import queue
from app.services.learner import projection_config, settings_of
from app.store.models import (
    Attempt,
    Exercise,
    ItemMemory,
    Learner,
    LearnerItem,
    LearningEvent,
    Placement,
    ReadingSession,
)

WINDOWS = (7, 30)


def learner_stats(db: Session, learner: Learner, now: datetime) -> dict[str, Any]:
    cfg = projection_config(settings_of(learner))
    since7, since30 = now - timedelta(days=7), now - timedelta(days=30)

    def reviews(since: datetime) -> int:
        return (
            db.scalar(
                select(func.count())
                .select_from(LearningEvent)
                .where(
                    LearningEvent.learner_id == learner.id,
                    LearningEvent.kind == "review",
                    LearningEvent.voided_by.is_(None),
                    LearningEvent.context.is_(None),
                    LearningEvent.ts >= since,
                    LearningEvent.ts <= now,
                )
            )
            or 0
        )

    placement_ids = select(Placement.id).where(Placement.learner_id == learner.id)
    sessions_7d = (
        db.scalar(
            select(func.count(distinct(Exercise.session_id)))
            .join(Attempt, Attempt.exercise_id == Exercise.id)
            .where(
                Exercise.learner_id == learner.id,
                Exercise.session_id.not_in(placement_ids),
                Attempt.submitted_at >= since7,
                Attempt.submitted_at <= now,
            )
        )
        or 0
    )
    readings_7d = (
        db.scalar(
            select(func.count())
            .select_from(ReadingSession)
            .where(ReadingSession.finished_at >= since7, ReadingSession.finished_at <= now)
        )
        or 0
    )
    new_items_7d = (
        db.scalar(
            select(func.count())
            .select_from(LearnerItem)
            .where(
                LearnerItem.learner_id == learner.id,
                LearnerItem.introduced_at >= since7,
                LearnerItem.introduced_at <= now,
            )
        )
        or 0
    )

    rows = db.execute(
        select(
            LearningEvent.kind,
            LearningEvent.outcome,
            LearningEvent.predicted_retrievability,
        ).where(
            LearningEvent.learner_id == learner.id,
            LearningEvent.kind == "review",
            LearningEvent.voided_by.is_(None),
            LearningEvent.predicted_retrievability.is_not(None),
        )
    ).all()
    memories = [
        MemoryView(
            item_id=m.item_id,
            facet=m.facet,
            due=m.due,
            card=Card.from_dict(m.fsrs_card) if m.fsrs_card else None,
        )
        for m in db.scalars(select(ItemMemory).where(ItemMemory.learner_id == learner.id))
    ]
    report = retention_report([RetentionEvent(*row) for row in rows], memories, cfg, now)
    return {
        "reviews_7d": reviews(since7),
        "reviews_30d": reviews(since30),
        "sessions_7d": sessions_7d,
        "readings_7d": readings_7d,
        "new_items_7d": new_items_7d,
        "observed_retention": report.observed_retention,
        "target_retention": report.target_retention,
        "calibration": [
            {
                "bucket_low": b.low,
                "bucket_high": b.high,
                "n": b.n,
                "predicted": b.predicted,
                "observed": b.observed,
                "recalled": b.recalled,
                "assisted": b.assisted,
                "forgotten": b.forgotten,
            }
            for b in report.calibration
        ],
        "backlog": queue.backlog(db, learner.id, now),
    }
