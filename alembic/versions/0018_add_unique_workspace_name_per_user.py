"""Add unique constraint on (user_id, name) to workspaces table

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-08 00:00:00.000000 UTC
"""
from typing import Sequence, Union

from alembic import op

# ── Revision identifiers ───────────────────────────────────────────────────────
revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("workspaces") as batch_op:
            batch_op.create_unique_constraint(
                "uq_workspaces_user_id_name",
                ["user_id", "name"],
            )
    else:
        op.create_unique_constraint(
            "uq_workspaces_user_id_name",
            "workspaces",
            ["user_id", "name"],
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("workspaces") as batch_op:
            batch_op.drop_constraint(
                "uq_workspaces_user_id_name",
                type_="unique",
            )
    else:
        op.drop_constraint(
            "uq_workspaces_user_id_name",
            "workspaces",
            type_="unique",
        )
