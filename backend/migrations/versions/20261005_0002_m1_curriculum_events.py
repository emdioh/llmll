"""curriculum items, event log and projections

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("cefr_level", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("interference", sa.JSON(), nullable=False),
        sa.Column("frequency_zipf", sa.Float(), nullable=True),
        sa.Column("source_file", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("suspended", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
    )
    op.create_table(
        "exercises",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("learner_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("prompt", sa.JSON(), nullable=False),
        sa.Column("solution", sa.JSON(), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("generator", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["learners.id"], name=op.f("fk_exercises_learner_id_learners")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exercises")),
    )
    with op.batch_alter_table("exercises", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_exercises_session_id"), ["session_id"], unique=False)

    op.create_table(
        "item_memory",
        sa.Column("learner_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("facet", sa.String(), nullable=False),
        sa.Column("fsrs_card", sa.JSON(), nullable=True),
        sa.Column("due", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stability", sa.Float(), nullable=True),
        sa.Column("difficulty", sa.Float(), nullable=True),
        sa.Column("mastery", sa.Float(), nullable=False),
        sa.Column("n_effective", sa.Float(), nullable=False),
        sa.Column("tag_error_counts", sa.JSON(), nullable=False),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.Column("projection_version", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_item_memory_item_id_items")
        ),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["learners.id"], name=op.f("fk_item_memory_learner_id_learners")
        ),
        sa.PrimaryKeyConstraint("learner_id", "item_id", "facet", name=op.f("pk_item_memory")),
    )
    with op.batch_alter_table("item_memory", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_item_memory_due"), ["due"], unique=False)

    op.create_table(
        "item_prerequisites",
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("requires_item_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_item_prerequisites_item_id_items")
        ),
        sa.ForeignKeyConstraint(
            ["requires_item_id"],
            ["items.id"],
            name=op.f("fk_item_prerequisites_requires_item_id_items"),
        ),
        sa.PrimaryKeyConstraint("item_id", "requires_item_id", name=op.f("pk_item_prerequisites")),
    )
    op.create_table(
        "learner_items",
        sa.Column("learner_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("candidate_source", sa.String(), nullable=True),
        sa.Column("candidate_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("introduced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_learner_items_item_id_items")
        ),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["learners.id"], name=op.f("fk_learner_items_learner_id_learners")
        ),
        sa.PrimaryKeyConstraint("learner_id", "item_id", name=op.f("pk_learner_items")),
    )
    op.create_table(
        "attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exercise_id", sa.String(), nullable=False),
        sa.Column("answer", sa.JSON(), nullable=False),
        sa.Column("used_hint", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["exercise_id"], ["exercises.id"], name=op.f("fk_attempts_exercise_id_exercises")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attempts")),
    )
    op.create_table(
        "learning_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("facet", sa.String(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("evidence_weight", sa.Float(), nullable=False),
        sa.Column("diagnostic_tags", sa.JSON(), nullable=False),
        sa.Column("presumed_known", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("exercise_id", sa.String(), nullable=True),
        sa.Column("attempt_id", sa.Integer(), nullable=True),
        sa.Column("evaluation_id", sa.Integer(), nullable=True),
        sa.Column("voided_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], name=op.f("fk_learning_events_attempt_id_attempts")
        ),
        sa.ForeignKeyConstraint(
            ["exercise_id"], ["exercises.id"], name=op.f("fk_learning_events_exercise_id_exercises")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_learning_events_item_id_items")
        ),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["learners.id"], name=op.f("fk_learning_events_learner_id_learners")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_events")),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table("learning_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_learning_events_lookup",
            ["learner_id", "item_id", "facet", "ts", "id"],
            unique=False,
        )

    with op.batch_alter_table("learners", schema=None) as batch_op:
        batch_op.add_column(sa.Column("level", sa.String(), server_default="A1", nullable=False))
        batch_op.add_column(
            sa.Column("known_languages", sa.JSON(), server_default=sa.text("'[]'"), nullable=False)
        )
        batch_op.add_column(
            sa.Column("explanation_language", sa.String(), server_default="it", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("learners", schema=None) as batch_op:
        batch_op.drop_column("explanation_language")
        batch_op.drop_column("known_languages")
        batch_op.drop_column("level")

    with op.batch_alter_table("learning_events", schema=None) as batch_op:
        batch_op.drop_index("ix_learning_events_lookup")

    op.drop_table("learning_events")
    op.drop_table("attempts")
    op.drop_table("learner_items")
    op.drop_table("item_prerequisites")
    with op.batch_alter_table("item_memory", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_item_memory_due"))

    op.drop_table("item_memory")
    with op.batch_alter_table("exercises", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_exercises_session_id"))

    op.drop_table("exercises")
    op.drop_table("items")
