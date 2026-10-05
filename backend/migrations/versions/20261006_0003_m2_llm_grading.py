"""LLM call log, evaluations, remediation queue, explanations, exercise status

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("exercises", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(), server_default="ready", nullable=False)
        )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("stop_reason", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_table(
        "evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("llm_call_id", sa.Integer(), nullable=True),
        sa.Column("lt_matches", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("grader_version", sa.String(), nullable=False),
        sa.Column("supersedes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], name=op.f("fk_evaluations_attempt_id_attempts")
        ),
        sa.ForeignKeyConstraint(
            ["llm_call_id"], ["llm_calls.id"], name=op.f("fk_evaluations_llm_call_id_llm_calls")
        ),
        sa.ForeignKeyConstraint(
            ["supersedes"], ["evaluations.id"], name=op.f("fk_evaluations_supersedes_evaluations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluations")),
    )
    op.create_table(
        "remediation_queue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("learner_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("diagnostic_tags", sa.JSON(), nullable=False),
        sa.Column("evaluation_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluations.id"],
            name=op.f("fk_remediation_queue_evaluation_id_evaluations"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_remediation_queue_item_id_items")
        ),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["learners.id"], name=op.f("fk_remediation_queue_learner_id_learners")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_remediation_queue")),
    )
    op.create_table(
        "explanations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("evaluation_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("llm_call_id", sa.Integer(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("examples", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluations.id"],
            name=op.f("fk_explanations_evaluation_id_evaluations"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_explanations_item_id_items")
        ),
        sa.ForeignKeyConstraint(
            ["llm_call_id"], ["llm_calls.id"], name=op.f("fk_explanations_llm_call_id_llm_calls")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_explanations")),
        sa.UniqueConstraint("evaluation_id", "item_id", name=op.f("uq_explanations_evaluation_id")),
    )


def downgrade() -> None:
    op.drop_table("explanations")
    op.drop_table("remediation_queue")
    op.drop_table("evaluations")
    op.drop_table("llm_calls")
    with op.batch_alter_table("exercises", schema=None) as batch_op:
        batch_op.drop_column("status")
