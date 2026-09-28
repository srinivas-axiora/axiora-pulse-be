"""Persist support tickets and their action/email history; preserve legacy JSON tickets."""
import json
from datetime import datetime, timezone
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    tickets = op.create_table(
        "support_tickets",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("user_email", sa.String(255), nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_tickets_user_id", "support_tickets", ["user_id"])
    op.create_index("ix_support_tickets_user_email", "support_tickets", ["user_email"])
    op.create_table(
        "support_ticket_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticket_id", sa.String(40), sa.ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient", sa.String(255)),
        sa.Column("email_payload", sa.JSON),
        sa.Column("email_status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
    )
    op.create_index("ix_support_ticket_events_ticket_id", "support_ticket_events", ["ticket_id"])
    op.create_index("ix_support_ticket_events_email_status", "support_ticket_events", ["email_status"])
    legacy = Path(__file__).resolve().parents[2] / "data" / "tickets.json"
    if legacy.exists():
        records = json.loads(legacy.read_text(encoding="utf-8"))
        connection = op.get_bind()
        for item in records:
            email = str(item.get("userEmail", "")).strip().lower()
            owner = connection.execute(sa.text("SELECT id FROM users WHERE lower(username) = :email"), {"email": email}).scalar()
            if owner is not None:
                item["userId"] = str(owner)
            connection.execute(tickets.insert().values(
                id=item["id"], user_id=owner, user_email=email, data=item,
                updated_at=datetime.fromisoformat(item["updatedAt"]) if item.get("updatedAt") else datetime.now(timezone.utc),
            ))


def downgrade():
    op.drop_table("support_ticket_events")
    op.drop_table("support_tickets")
