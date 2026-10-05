"""create learners

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learners")),
    )


def downgrade() -> None:
    op.drop_table("learners")
