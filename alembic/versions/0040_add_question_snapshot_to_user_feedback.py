"""Snapshot the question text on user feedback submissions.

Admin edits to a feedback template (`feedback_questionnaires.question`) used to
rewrite what earlier respondents are shown: `user_feedback_questionnaires` only
stored the template id, and the admin directory re-joined to the *current* question
text at read time.

This adds `question_snapshot` — the question text as it was when the user submitted
— stored on each response row. It is back-filled from the template for existing rows,
then made NOT NULL so every submission carries an immutable copy of the question.

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
        "user_feedback_questionnaires",
        sa.Column("question_snapshot", sa.Text(), nullable=True),
    )
    op.execute("""
        UPDATE user_feedback_questionnaires u
        SET question_snapshot = f.question
        FROM feedback_questionnaires f
        WHERE u.questionnaire_id = f.id
          AND u.question_snapshot IS NULL
    """)
    op.alter_column(
        "user_feedback_questionnaires",
        "question_snapshot",
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("user_feedback_questionnaires", "question_snapshot")