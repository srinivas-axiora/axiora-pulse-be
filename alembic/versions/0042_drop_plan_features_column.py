"""Drop the `features` column from plans and add `old_price`.

The feature list was a marketing-style bullet list no longer part of the plan
model — the per-plan quota columns (workspace_limit, survey_response_cap, etc.)
are the single source of truth for what each tier allows. Removes the column and
its seed values historically written by 0022.

Adds `old_price`: the pre-discount (strikethrough) monthly price shown on the
pricing page. Seeded per tier code for every environment that reaches `upgrade
head`:

  code      old_price
  starter   499
  builder   999
  pro       1999

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-22
"""
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE plans DROP COLUMN IF EXISTS features")
    op.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS old_price INTEGER")
    op.execute("UPDATE plans SET old_price = 499 WHERE code = 'starter'")
    op.execute("UPDATE plans SET old_price = 999 WHERE code = 'builder'")
    op.execute("UPDATE plans SET old_price = 1999 WHERE code = 'pro'")


def downgrade() -> None:
    op.execute("ALTER TABLE plans DROP COLUMN IF EXISTS old_price")
    op.execute("""
        ALTER TABLE plans
        ADD COLUMN IF NOT EXISTS features JSON NOT NULL DEFAULT '[]'::json
    """)