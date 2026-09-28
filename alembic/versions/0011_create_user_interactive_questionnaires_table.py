"""Create user_interactive_questionnaires table

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-28 00:00:00.000000 UTC
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ── Revision identifiers ───────────────────────────────────────────────────────
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user_interactive_questionnaires table."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON()
    json_default = sa.text("'[]'::jsonb") if is_postgres else sa.text("'[]'")

    op.create_table(
        "user_interactive_questionnaires",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("questionnaire_id", sa.Integer(), nullable=False),
        sa.Column(
            "user_answers",
            json_type,
            nullable=False,
            server_default=json_default,
        ),
        sa.Column(
            "submission_date",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["questionnaire_id"],
            ["interactive_questionnaires.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_interactive_questionnaires_user_id_questionnaire_id"),
        "user_interactive_questionnaires",
        ["user_id", "questionnaire_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop user_interactive_questionnaires table."""
    op.drop_index(
        op.f("ix_user_interactive_questionnaires_user_id_questionnaire_id"),
        table_name="user_interactive_questionnaires",
    )
    op.drop_table("user_interactive_questionnaires")
