"""llm_calls.provider

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("provider", sa.String(), server_default="anthropic", nullable=False)
        )
    # Existing rows were written by the Anthropic client or by the fake one.
    op.execute("UPDATE llm_calls SET provider = 'fake' WHERE model = 'fake'")


def downgrade() -> None:
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.drop_column("provider")
