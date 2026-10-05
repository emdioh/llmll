"""Learner setup, level placement and settings."""

from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.curriculum.schema import CEFR_LEVELS
from app.domain.config import LearnerSettings, ProjectionConfig
from app.store.events import rebuild_projection
from app.store.models import Item, Learner, LearnerItem, LearningEvent

LEARNER_ID = 1
REPLACEABLE_STATUSES = ("unseen", "presumed_known", "candidate")


class LearnerExistsError(Exception):
    pass


def level_rank(level: str) -> int:
    return CEFR_LEVELS.index(level)


def initial_status(kind: str, item_level: str, declared_level: str) -> tuple[str, str | None]:
    """Initial `(status, candidate_source)` for an item without events (design §4.1)."""
    if level_rank(item_level) < level_rank(declared_level):
        return "presumed_known", None
    if kind == "lemma" and item_level == declared_level:
        return "candidate", "wordlist"
    return "unseen", None


def settings_of(learner: Learner) -> LearnerSettings:
    defaults = asdict(LearnerSettings())
    stored: dict[str, Any] = {k: v for k, v in (learner.settings or {}).items() if k in defaults}
    return LearnerSettings(**{**defaults, **stored})


def projection_config(settings: LearnerSettings) -> ProjectionConfig:
    return ProjectionConfig(desired_retention=settings.desired_retention)


def create_learner(
    session: Session,
    level: str,
    known_languages: list[str],
    explanation_language: str,
    now: datetime,
) -> Learner:
    if session.get(Learner, LEARNER_ID) is not None:
        raise LearnerExistsError
    learner = Learner(
        id=LEARNER_ID,
        level=level,
        known_languages=known_languages,
        explanation_language=explanation_language,
        settings=asdict(LearnerSettings()),
        created_at=now,
    )
    session.add(learner)
    session.flush()
    for item in session.scalars(select(Item)):
        status, source = initial_status(item.kind, item.cefr_level, level)
        session.add(
            LearnerItem(
                learner_id=LEARNER_ID,
                item_id=item.id,
                status=status,
                candidate_source=source,
                candidate_since=now if status == "candidate" else None,
            )
        )
    session.commit()
    return learner


def update_learner(
    session: Session,
    learner: Learner,
    now: datetime,
    level: str | None = None,
    known_languages: list[str] | None = None,
    explanation_language: str | None = None,
) -> Learner:
    if known_languages is not None:
        learner.known_languages = known_languages
    if explanation_language is not None:
        learner.explanation_language = explanation_language
    if level is not None and level != learner.level:
        learner.level = level
        with_events = set(
            session.scalars(
                select(LearningEvent.item_id).where(LearningEvent.learner_id == learner.id)
            )
        )
        rows = session.execute(
            select(LearnerItem, Item)
            .join(Item, Item.id == LearnerItem.item_id)
            .where(
                LearnerItem.learner_id == learner.id,
                LearnerItem.status.in_(REPLACEABLE_STATUSES),
            )
        )
        for learner_item, item in rows:
            if item.id in with_events:
                continue
            if learner_item.status == "candidate" and learner_item.candidate_source != "wordlist":
                continue  # explicit candidates (opt-in, article) are kept
            status, source = initial_status(item.kind, item.cefr_level, level)
            learner_item.status = status
            learner_item.candidate_source = source
            learner_item.candidate_since = now if status == "candidate" else None
    session.commit()
    return learner


def update_settings(session: Session, learner: Learner, changes: dict[str, Any]) -> LearnerSettings:
    """Merge settings; a changed projection version triggers a full replay."""
    before = settings_of(learner)
    after = LearnerSettings(**{**asdict(before), **changes})
    learner.settings = asdict(after)
    if projection_config(before).version != projection_config(after).version:
        rebuild_projection(session, learner.id, projection_config(after))
    session.commit()
    return after
