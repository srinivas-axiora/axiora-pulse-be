"""Authenticated tickets with atomic action history and email outbox."""
from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_current_user, require_admin
from app.db.database import get_db
from app.db.models import SupportTicket, SupportTicketEvent, User

router = APIRouter(prefix="/tickets", tags=["Support Tickets"])

class CreateTicketPayload(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=20000)
    priority: Literal["Low", "Medium", "High"] = "Medium"
    attachmentName: str | None = Field(default=None, max_length=255)

class ReplyPayload(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    attachmentName: str | None = Field(default=None, max_length=255)

class NotePayload(BaseModel):
    note: str = Field(min_length=1, max_length=20000)

class StatusPayload(BaseModel):
    status: Literal["Open", "In Progress", "Resolved", "Closed"]

class AssignPayload(BaseModel):
    agentName: str = Field(min_length=1, max_length=100, pattern=r"\S")

def _name(user):
    return user.display_name or user.username

def _visible(ticket, user):
    data = deepcopy(ticket.data)
    if not user.has_role("admin"):
        data["internalNotes"] = []
    return data

async def _ticket(ticket_id, user, db):
    ticket = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id).with_for_update())).scalar_one_or_none()
    if ticket is None or (not user.has_role("admin") and ticket.user_id != user.id and not (
        ticket.user_id is None and ticket.user_email == user.username.lower()
    )):
        raise HTTPException(404, "Ticket not found")
    return ticket

def _message(user, text, *, system=False, attachment=None):
    return {
        "id": str(uuid4()), "senderId": "system" if system else str(user.id),
        "senderName": "System" if system else _name(user),
        "senderEmail": "" if system else user.username,
        "senderRole": "admin" if user.has_role("admin") else "user",
        "message": text, "timestamp": datetime.now(timezone.utc).isoformat(),
        "attachmentName": attachment,
    }

async def _save(ticket, data, user, db, action, details, notification=None):
    now = datetime.now(timezone.utc)
    data["updatedAt"] = now.isoformat()
    ticket.data = data
    ticket.updated_at = now
    payload = None
    if notification is not None:
        payload = {"ticket_id": ticket.id, "subject": data["subject"],
                   "display_name": data["userName"], "update": notification}
    db.add(SupportTicketEvent(
        ticket_id=ticket.id, actor_id=user.id, action=action,
        details={**details, "actorName": _name(user)},
        recipient=ticket.user_email if payload else None, email_payload=payload,
        email_status="pending" if payload else "not_required",
    ))
    # Commit the action and outbox together before returning success.
    await db.commit()
    return _visible(ticket, user)

@router.get("")
async def get_tickets(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = select(SupportTicket).order_by(SupportTicket.updated_at.desc())
    if not user.has_role("admin"):
        query = query.where(or_(SupportTicket.user_id == user.id,
            (SupportTicket.user_id.is_(None)) & (SupportTicket.user_email == user.username.lower())))
    return [_visible(t, user) for t in (await db.execute(query)).scalars()]

@router.post("")
async def create_ticket(payload: CreateTicketPayload, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    ticket_id = f"TCK-{uuid4().hex}"
    data = {**payload.model_dump(), "id": ticket_id, "userId": str(user.id),
            "userName": _name(user), "userEmail": user.username, "status": "Open",
            "createdAt": now.isoformat(), "updatedAt": now.isoformat(),
            "unreadByUser": False, "unreadByAdmin": True,
            "messages": [_message(user, payload.description, attachment=payload.attachmentName)],
            "internalNotes": []}
    ticket = SupportTicket(id=ticket_id, user_id=user.id, user_email=user.username.lower(), data=data)
    db.add(ticket)
    await db.flush()
    return await _save(ticket, data, user, db, "created", {})

@router.post("/{ticket_id}/reply")
async def add_reply(ticket_id: str, payload: ReplyPayload, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ticket = await _ticket(ticket_id, user, db)
    data = deepcopy(ticket.data)
    admin = user.has_role("admin")
    data.update(unreadByUser=admin, unreadByAdmin=not admin)
    data["messages"].append(_message(user, payload.message, attachment=payload.attachmentName))
    return await _save(ticket, data, user, db, "admin_reply" if admin else "user_reply",
                       {"message": payload.message, "attachmentName": payload.attachmentName},
                       f"Our support team replied:\n\n{payload.message}" if admin else None)

@router.post("/{ticket_id}/note")
async def add_internal_note(ticket_id: str, payload: NotePayload, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    ticket = await _ticket(ticket_id, user, db)
    data = deepcopy(ticket.data)
    data["internalNotes"].append({"id": str(uuid4()), "adminId": str(user.id),
        "adminName": _name(user), "note": payload.note, "timestamp": datetime.now(timezone.utc).isoformat()})
    return await _save(ticket, data, user, db, "internal_note", {"note": payload.note})

@router.patch("/{ticket_id}/status")
async def update_status(ticket_id: str, payload: StatusPayload, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    ticket = await _ticket(ticket_id, user, db)
    data = deepcopy(ticket.data)
    old = data["status"]
    if old == payload.status:
        return _visible(ticket, user)
    data.update(status=payload.status, unreadByUser=True)
    text = f"Status changed from {old} to {payload.status}."
    data["messages"].append(_message(user, f"{text} Updated by {_name(user)}", system=True))
    return await _save(ticket, data, user, db, "status_changed", {"old": old, "new": payload.status}, text)

@router.patch("/{ticket_id}/assign")
async def assign_agent(ticket_id: str, payload: AssignPayload, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    ticket = await _ticket(ticket_id, user, db)
    data = deepcopy(ticket.data)
    old = data.get("assignedTo", "Unassigned")
    agent = payload.agentName.strip()
    if old == agent:
        return _visible(ticket, user)
    data.update(assignedTo=agent, unreadByUser=True)
    text = "Your ticket is awaiting assignment." if agent == "Unassigned" else f"Your ticket has been assigned to {agent}."
    data["messages"].append(_message(user, f"{text} Updated by {_name(user)}", system=True))
    return await _save(ticket, data, user, db, "assigned", {"old": old, "new": agent}, text)

async def _read(ticket_id, user, db, key):
    ticket = await _ticket(ticket_id, user, db)
    ticket.data = {**ticket.data, key: False}
    await db.commit()
    return {"status": "ok"}

@router.patch("/{ticket_id}/read-user")
async def mark_user_read(ticket_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _read(ticket_id, user, db, "unreadByUser")

@router.patch("/{ticket_id}/read-admin")
async def mark_admin_read(ticket_id: str, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await _read(ticket_id, user, db, "unreadByAdmin")
