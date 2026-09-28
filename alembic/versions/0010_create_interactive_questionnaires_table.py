"""Create interactive_questionnaires table

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-28 00:00:00.000000 UTC
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ── Revision identifiers ───────────────────────────────────────────────────────
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create interactive_questionnaires table."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON()
    json_default = sa.text("'[]'::jsonb") if is_postgres else sa.text("'[]'")

    op.create_table(
        "interactive_questionnaires",
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
            name="ck_interactive_questionnaires_answer_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_interactive_questionnaires_id"),
        "interactive_questionnaires",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_interactive_questionnaires_answer_type"),
        "interactive_questionnaires",
        ["answer_type"],
        unique=False,
    )


def downgrade() -> None:
    """Drop interactive_questionnaires table."""
    op.drop_index(op.f("ix_interactive_questionnaires_answer_type"), table_name="interactive_questionnaires")
    op.drop_index(op.f("ix_interactive_questionnaires_id"), table_name="interactive_questionnaires")
    op.drop_table("interactive_questionnaires")
