"""Add stage_rerun / survey_analytics / storage_limit to plans, with per-plan seed values.

Extends the per-plan feature surface for the admin Plans management endpoints:
- stage_rerun ── integer cap for stage re-runs; NULL = not allowed / unlimited.
- survey_analytics ── analytics depth tier, one of Basic | Advanced (defaults Basic).
- storage_limit ── storage allowance in MB; NULL = not enforced.

The per-plan values are seeded here (keyed by plan code) so every environment
that reaches `upgrade head` gets identical, reproducible data:

  code      stage_rerun  survey_analytics  storage_limit
  starter   1            Basic                200
  builder   3            Advanced             500
  pro       5            Advanced            2000

Only existing rows are updated (WHERE code = ...), so this is safe for the
dev DB and any other environment that already has the three standard plans.

export_validation_reports is intentionally NOT created here — that capability
is covered by the pre-existing `export_enabled` column.

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("stage_rerun", sa.Integer(), nullable=True),
    )
    op.add_column(
        "plans",
        sa.Column(
            "survey_analytics",
            sa.String(20),
            nullable=False,
            server_default="Basic",
        ),
    )
    op.add_column(
        "plans",
        sa.Column("storage_limit", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_plans_survey_analytics",
        "plans",
        "survey_analytics IN ('Basic', 'Advanced')",
    )

    # Seed the per-plan feature values.
    op.execute(
        """
        UPDATE plans SET
            stage_rerun = 1,
            survey_analytics = 'Basic',
            storage_limit = 200
        WHERE code = 'starter'
        """
    )
    op.execute(
        """
        UPDATE plans SET
            stage_rerun = 3,
            survey_analytics = 'Advanced',
            storage_limit = 500
        WHERE code = 'builder'
        """
    )
    op.execute(
        """
        UPDATE plans SET
            stage_rerun = 5,
            survey_analytics = 'Advanced',
            storage_limit = 2000
        WHERE code = 'pro'
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_plans_survey_analytics", "plans", type_="check")
    op.drop_column("plans", "survey_analytics")
    op.drop_column("plans", "stage_rerun")
    op.drop_column("plans", "storage_limit")