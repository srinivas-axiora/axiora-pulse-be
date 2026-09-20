"""Add export_validation_reports / stage_rerun / survey_analytics to plans.

Extends the per-plan feature surface for the admin Plans management endpoints:
- export_validation_reports ── boolean, whether validation reports can be exported
  (defaults ON so existing behavior is unchanged).
- stage_rerun ── integer cap for stage re-runs; NULL = not allowed / unlimited.
- survey_analytics ── analytics depth tier, one of Basic | Advanced (defaults Basic).

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column(
            "export_validation_reports",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
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
    op.create_check_constraint(
        "ck_plans_survey_analytics",
        "plans",
        "survey_analytics IN ('Basic', 'Advanced')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_plans_survey_analytics", "plans", type_="check")
    op.drop_column("plans", "survey_analytics")
    op.drop_column("plans", "stage_rerun")
    op.drop_column("plans", "export_validation_reports")