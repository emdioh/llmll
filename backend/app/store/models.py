"""SQLAlchemy models."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes, also on SQLite (which stores them naive)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Learner(Base):
    __tablename__ = "learners"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    level: Mapped[str] = mapped_column(String, server_default="A1")
    known_languages: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    explanation_language: Mapped[str] = mapped_column(String, server_default="it")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    cefr_level: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    interference: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    frequency_zipf: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_file: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String)
    suspended: Mapped[bool] = mapped_column(default=False)


class ItemPrerequisite(Base):
    __tablename__ = "item_prerequisites"

    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    requires_item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)


class LearnerItem(Base):
    __tablename__ = "learner_items"

    learner_id: Mapped[int] = mapped_column(ForeignKey("learners.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String)
    candidate_source: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_since: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    introduced_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    learner_id: Mapped[int] = mapped_column(ForeignKey("learners.id"))
    type: Mapped[str] = mapped_column(String)
    prompt: Mapped[dict[str, Any]] = mapped_column(JSON)
    solution: Mapped[dict[str, Any]] = mapped_column(JSON)
    targets: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    generator: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    session_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="ready", server_default="ready")


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    exercise_id: Mapped[str] = mapped_column(ForeignKey("exercises.id"))
    answer: Mapped[dict[str, Any]] = mapped_column(JSON)
    used_hint: Mapped[bool] = mapped_column(default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime())


class LearningEvent(Base):
    """Append-only event log. Only `voided_by` may ever be updated."""

    __tablename__ = "learning_events"
    __table_args__ = (
        Index("ix_learning_events_lookup", "learner_id", "item_id", "facet", "ts", "id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    learner_id: Mapped[int] = mapped_column(ForeignKey("learners.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"))
    facet: Mapped[str] = mapped_column(String)
    ts: Mapped[datetime] = mapped_column(UTCDateTime())
    kind: Mapped[str] = mapped_column(String)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_weight: Mapped[float] = mapped_column(Float, default=0.0)
    diagnostic_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    presumed_known: Mapped[bool] = mapped_column(default=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    exercise_id: Mapped[str | None] = mapped_column(ForeignKey("exercises.id"), nullable=True)
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("attempts.id"), nullable=True)
    evaluation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # null for normal events, "placement" for the initial assessment
    context: Mapped[str | None] = mapped_column(String, nullable=True)
    # FSRS retrievability of the card just before this review; null when no card existed yet
    predicted_retrievability: Mapped[float | None] = mapped_column(Float, nullable=True)


class ItemMemory(Base):
    """Projection of `learning_events`; rebuildable at any time."""

    __tablename__ = "item_memory"

    learner_id: Mapped[int] = mapped_column(ForeignKey("learners.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    facet: Mapped[str] = mapped_column(String, primary_key=True)
    fsrs_card: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    due: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    stability: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty: Mapped[float | None] = mapped_column(Float, nullable=True)
    mastery: Mapped[float] = mapped_column(Float)
    n_effective: Mapped[float] = mapped_column(Float)
    tag_error_counts: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    last_event_id: Mapped[int] = mapped_column(Integer)
    projection_version: Mapped[str] = mapped_column(String)


class LLMCall(Base):
    """Log of every LLM call, including failures (the grader evaluation dataset)."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime())
    task: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    response: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"))
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id"), nullable=True)
    lt_matches: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    grader_version: Mapped[str] = mapped_column(String)
    supersedes: Mapped[int | None] = mapped_column(ForeignKey("evaluations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class RemediationItem(Base):
    __tablename__ = "remediation_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    learner_id: Mapped[int] = mapped_column(ForeignKey("learners.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"))
    diagnostic_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    evaluation_id: Mapped[int | None] = mapped_column(ForeignKey("evaluations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class StoredExplanation(Base):
    """Cached error explanations, one per evaluation and item."""

    __tablename__ = "explanations"
    __table_args__ = (UniqueConstraint("evaluation_id", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"))
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id"), nullable=True)
    markdown: Mapped[str] = mapped_column(Text)
    examples: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SourceText(Base):
    """A source text (pasted, fetched or generated) of the learner."""

    __tablename__ = "texts"

    id: Mapped[int] = mapped_column(primary_key=True)
    learner_id: Mapped[int] = mapped_column(ForeignKey("learners.id"))
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    source_title: Mapped[str] = mapped_column(String)
    source_text: Mapped[str] = mapped_column(Text)
    source_language: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class TextVersion(Base):
    """One simplification attempt of a text; exactly one per text is `selected`."""

    __tablename__ = "text_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    text_id: Mapped[int] = mapped_column(ForeignKey("texts.id"), index=True)
    level: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    coverage: Mapped[float] = mapped_column(Float)
    attempt: Mapped[int] = mapped_column(Integer)
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id"), nullable=True)
    selected: Mapped[bool] = mapped_column(default=False)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ReadingSession(Base):
    __tablename__ = "reading_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    text_version_id: Mapped[int] = mapped_column(ForeignKey("text_versions.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    lookups: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class GlossCache(Base):
    """Cached LLM glosses of unlisted words, keyed by lemma and context."""

    __tablename__ = "glosses"
    __table_args__ = (UniqueConstraint("lemma", "context_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lemma: Mapped[str] = mapped_column(String)
    context_hash: Mapped[str] = mapped_column(String)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Contest(Base):
    """A contested evaluation and its resolution (design: M4 §2)."""

    __tablename__ = "contests"

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id"), index=True)
    item_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String)  # open | resolved
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    resolver: Mapped[str] = mapped_column(String)
    rationale: Mapped[str] = mapped_column(Text, default="", server_default="")
    replacement_evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluations.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Placement(Base):
    """One run of the initial assessment; its exercises have `session_id = id`."""

    __tablename__ = "placements"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    learner_id: Mapped[int] = mapped_column(ForeignKey("learners.id"))
    declared_level: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    estimated_level: Mapped[str | None] = mapped_column(String, nullable=True)
    changed: Mapped[bool | None] = mapped_column(nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
