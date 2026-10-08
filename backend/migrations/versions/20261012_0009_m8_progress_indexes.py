"""indexes for the progress review

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_attempts_submitted_at", "attempts", ("submitted_at",)),
    ("ix_attempts_exercise_id", "attempts", ("exercise_id",)),
    ("ix_evaluations_attempt_id", "evaluations", ("attempt_id",)),
    ("ix_learning_events_learner_ts", "learning_events", ("learner_id", "ts")),
    ("ix_learning_events_exercise_id", "learning_events", ("exercise_id",)),
)


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, list(columns))


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
