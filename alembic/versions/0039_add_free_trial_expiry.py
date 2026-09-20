"""Add free_trial_expires_at to user_allowed_workspaces (7-day free trial).

The free (Starter) tier is "1 workspace for 7 days". This column records when a
user's free trial ends; it is stamped when they select the free plan
(POST /api/billing/subscription/{plan_id}). A user counts as having an active plan
while `free_trial_expires_at` is in the future OR they hold an active paid
subscription — after which the route guard sends them to the pricing page.

NULL = no free trial started (e.g. paid-first users, or the row created lazily for
allowance counting) — those rely on their paid subscription for access.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_allowed_workspaces",
        sa.Column("free_trial_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_allowed_workspaces", "free_trial_expires_at")
