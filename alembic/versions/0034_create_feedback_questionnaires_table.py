"""Create feedback_questionnaires table

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ── Revision identifiers ───────────────────────────────────────────────────────
revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create feedback_questionnaires table."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON()
    json_default = sa.text("'[]'::jsonb") if is_postgres else sa.text("'[]'")

    op.create_table(
        "feedback_questionnaires",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer_type", sa.String(length=50), nullable=False),
        sa.Column("optional", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "answers",
            json_type,
            nullable=False,
            server_default=json_default,
        ),
        sa.Column(
            "is_display",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "answer_type IN ('textarea', 'radiobuttons', 'checkboxes', 'dropdown')",
            name="ck_feedback_questionnaires_answer_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_feedback_questionnaires_id"),
        "feedback_questionnaires",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_feedback_questionnaires_answer_type"),
        "feedback_questionnaires",
        ["answer_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feedback_questionnaires_is_display"),
        "feedback_questionnaires",
        ["is_display"],
        unique=False,
    )


def downgrade() -> None:
    """Drop feedback_questionnaires table."""
    op.drop_index(op.f("ix_feedback_questionnaires_is_display"), table_name="feedback_questionnaires")
    op.drop_index(op.f("ix_feedback_questionnaires_answer_type"), table_name="feedback_questionnaires")
    op.drop_index(op.f("ix_feedback_questionnaires_id"), table_name="feedback_questionnaires")
    op.drop_table("feedback_questionnaires")