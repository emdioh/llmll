"""llm_calls timing breakdown and token detail

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("attempts", sa.Integer()),
    ("http_statuses", sa.JSON()),
    ("retry_wait_ms", sa.Integer()),
    ("ttfb_ms", sa.Integer()),
    ("download_ms", sa.Integer()),
    ("overhead_ms", sa.Integer()),
    ("reasoning_tokens", sa.Integer()),
    ("upstream_provider", sa.String()),
    ("request_chars", sa.Integer()),
)


def upgrade() -> None:
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        for name, type_ in _COLUMNS:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        for name, _ in reversed(_COLUMNS):
            batch_op.drop_column(name)
