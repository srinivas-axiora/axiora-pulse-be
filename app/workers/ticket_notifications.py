"""Durable ticket email outbox. Pending work survives restarts.

Row locks prevent concurrent workers sending the same event. Delivery is at-least-once:
a crash after SMTP accepts a message but before commit can cause a duplicate.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.db.models import SupportTicketEvent
from app.services.email_service import send_ticket_update_email

logger = logging.getLogger(__name__)


async def deliver_pending(session_factory=AsyncSessionLocal) -> bool:
    async with session_factory() as db:
        async with db.begin():
            event = (await db.execute(
                select(SupportTicketEvent)
                .where(SupportTicketEvent.email_status == "pending",
                       SupportTicketEvent.next_attempt_at <= datetime.now(timezone.utc))
                .order_by(SupportTicketEvent.created_at)
                .with_for_update(skip_locked=True).limit(1)
            )).scalar_one_or_none()
            if event is None:
                return False
            event.attempts += 1
            try:
                result = await send_ticket_update_email(
                    to_email=event.recipient, event_id=event.id, **event.email_payload)
                success, error = result.success, result.error
            except Exception as exc:
                success, error = False, str(exc)
            if success:
                event.email_status = "sent"
                event.sent_at = datetime.now(timezone.utc)
                event.last_error = None
            else:
                event.last_error = (error or "Email delivery failed")[:2000]
                event.email_status = "failed" if event.attempts >= 3 else "pending"
                event.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=30 * event.attempts)
                logger.warning("Ticket email event=%s attempt=%s status=%s", event.id, event.attempts, event.email_status)
    return True


async def run_ticket_notifications():
    while True:
        try:
            if await deliver_pending():
                continue
        except Exception:
            logger.exception("Ticket notification worker failed; retrying")
        await asyncio.sleep(5)
