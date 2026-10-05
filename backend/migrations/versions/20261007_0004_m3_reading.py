"""Reading: texts, text versions, reading sessions, gloss cache

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "texts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("learner_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("source_title", sa.String(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_language", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["learners.id"], name=op.f("fk_texts_learner_id_learners")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_texts")),
    )
    op.create_table(
        "text_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("llm_call_id", sa.Integer(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["llm_call_id"], ["llm_calls.id"], name=op.f("fk_text_versions_llm_call_id_llm_calls")
        ),
        sa.ForeignKeyConstraint(
            ["text_id"], ["texts.id"], name=op.f("fk_text_versions_text_id_texts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_text_versions")),
    )
    with op.batch_alter_table("text_versions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_text_versions_text_id"), ["text_id"], unique=False)

    op.create_table(
        "reading_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text_version_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lookups", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["text_version_id"],
            ["text_versions.id"],
            name=op.f("fk_reading_sessions_text_version_id_text_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reading_sessions")),
    )
    with op.batch_alter_table("reading_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_reading_sessions_text_version_id"), ["text_version_id"], unique=False
        )

    op.create_table(
        "glosses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lemma", sa.String(), nullable=False),
        sa.Column("context_hash", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("llm_call_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["llm_call_id"], ["llm_calls.id"], name=op.f("fk_glosses_llm_call_id_llm_calls")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_glosses")),
        sa.UniqueConstraint("lemma", "context_hash", name=op.f("uq_glosses_lemma")),
    )


def downgrade() -> None:
    op.drop_table("glosses")
    with op.batch_alter_table("reading_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_reading_sessions_text_version_id"))
    op.drop_table("reading_sessions")
    with op.batch_alter_table("text_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_text_versions_text_id"))
    op.drop_table("text_versions")
    op.drop_table("texts")
