"""Contests, placements, learning_events.context

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("learning_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("context", sa.String(), nullable=True))

    op.create_table(
        "contests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("evaluation_id", sa.Integer(), nullable=False),
        sa.Column("item_ids", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=True),
        sa.Column("resolver", sa.String(), nullable=False),
        sa.Column("rationale", sa.Text(), server_default="", nullable=False),
        sa.Column("replacement_evaluation_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluations.id"],
            name=op.f("fk_contests_evaluation_id_evaluations"),
        ),
        sa.ForeignKeyConstraint(
            ["replacement_evaluation_id"],
            ["evaluations.id"],
            name=op.f("fk_contests_replacement_evaluation_id_evaluations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contests")),
    )
    with op.batch_alter_table("contests", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_contests_evaluation_id"), ["evaluation_id"])

    op.create_table(
        "placements",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("learner_id", sa.Integer(), nullable=False),
        sa.Column("declared_level", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_level", sa.String(), nullable=True),
        sa.Column("changed", sa.Boolean(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["learners.id"], name=op.f("fk_placements_learner_id_learners")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_placements")),
    )


def downgrade() -> None:
    op.drop_table("placements")
    with op.batch_alter_table("contests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contests_evaluation_id"))
    op.drop_table("contests")
    with op.batch_alter_table("learning_events", schema=None) as batch_op:
        batch_op.drop_column("context")
