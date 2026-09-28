import json
import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/tickets", tags=["Support Tickets"])

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "tickets.json")

def _load_tickets() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_tickets(tickets: list) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(tickets, f, indent=2)

class UserInfo(BaseModel):
    id: Optional[str] = "user-guest"
    name: Optional[str] = "User"
    email: Optional[str] = "user@example.com"

class CreateTicketPayload(BaseModel):
    category: str
    subject: str
    description: str
    priority: Optional[str] = "Medium"
    attachmentName: Optional[str] = None
    user: UserInfo

class ReplyPayload(BaseModel):
    message: str
    attachmentName: Optional[str] = None
    sender: UserInfo
    senderRole: str  # "user" or "admin"

class NotePayload(BaseModel):
    note: str
    admin: UserInfo

class StatusPayload(BaseModel):
    status: str
    adminName: str

class AssignPayload(BaseModel):
    agentName: str
    adminName: str


@router.get("")
async def get_tickets(
    user_id: Optional[str] = Query(None),
    user_email: Optional[str] = Query(None),
):
    tickets = _load_tickets()
    if user_id or user_email:
        filtered = []
        for t in tickets:
            match_id = user_id and str(t.get("userId")) == str(user_id)
            match_email = (
                user_email
                and str(t.get("userEmail", "")).lower() == str(user_email).lower()
            )
            if match_id or match_email:
                filtered.append(t)
        return filtered
    return tickets


@router.post("")
async def create_ticket(payload: CreateTicketPayload):
    tickets = _load_tickets()
    counter = 1001 + len(tickets)
    ticket_id = f"TCK-{counter}"
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    new_ticket = {
        "id": ticket_id,
        "userId": str(payload.user.id or "user-guest"),
        "userName": payload.user.name or "User",
        "userEmail": payload.user.email or "user@example.com",
        "category": payload.category,
        "subject": payload.subject,
        "description": payload.description,
        "attachmentName": payload.attachmentName,
        "priority": payload.priority or "Medium",
        "status": "Open",
        "createdAt": now,
        "updatedAt": now,
        "unreadByUser": False,
        "unreadByAdmin": True,
        "messages": [
            {
                "id": f"msg-{int(datetime.datetime.now().timestamp() * 1000)}",
                "senderId": str(payload.user.id or "user-guest"),
                "senderName": payload.user.name or "User",
                "senderEmail": payload.user.email or "user@example.com",
                "senderRole": "user",
                "message": payload.description,
                "timestamp": now,
                "attachmentName": payload.attachmentName,
            }
        ],
        "internalNotes": [],
    }

    tickets.insert(0, new_ticket)
    _save_tickets(tickets)
    return new_ticket


@router.post("/{ticket_id}/reply")
async def add_reply(ticket_id: str, payload: ReplyPayload):
    tickets = _load_tickets()
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    msg_id = f"msg-{int(datetime.datetime.now().timestamp() * 1000)}"

    found = None
    for t in tickets:
        if t["id"] == ticket_id:
            found = t
            t["updatedAt"] = now
            if payload.senderRole == "admin":
                t["unreadByUser"] = True
                t["unreadByAdmin"] = False
            else:
                t["unreadByAdmin"] = True
                t["unreadByUser"] = False

            t["messages"].append({
                "id": msg_id,
                "senderId": str(payload.sender.id or "guest"),
                "senderName": payload.sender.name or "Support",
                "senderEmail": payload.sender.email or "support@axiorapulse.com",
                "senderRole": payload.senderRole,
                "message": payload.message,
                "timestamp": now,
                "attachmentName": payload.attachmentName,
            })
            break

    if not found:
        raise HTTPException(status_code=404, detail="Ticket not found")

    _save_tickets(tickets)
    return found


@router.post("/{ticket_id}/note")
async def add_internal_note(ticket_id: str, payload: NotePayload):
    tickets = _load_tickets()
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    note_id = f"note-{int(datetime.datetime.now().timestamp() * 1000)}"

    found = None
    for t in tickets:
        if t["id"] == ticket_id:
            found = t
            t["internalNotes"].append({
                "id": note_id,
                "adminId": str(payload.admin.id or "admin-1"),
                "adminName": payload.admin.name or "Support Admin",
                "note": payload.note,
                "timestamp": now,
            })
            break

    if not found:
        raise HTTPException(status_code=404, detail="Ticket not found")

    _save_tickets(tickets)
    return found


@router.patch("/{ticket_id}/status")
async def update_status(ticket_id: str, payload: StatusPayload):
    tickets = _load_tickets()
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    msg_id = f"msg-status-{int(datetime.datetime.now().timestamp() * 1000)}"

    found = None
    for t in tickets:
        if t["id"] == ticket_id:
            found = t
            t["status"] = payload.status
            t["updatedAt"] = now
            t["unreadByUser"] = True
            t["messages"].append({
                "id": msg_id,
                "senderId": "system",
                "senderName": "System",
                "senderEmail": "",
                "senderRole": "admin",
                "message": f"Status changed to {payload.status} by {payload.adminName}",
                "timestamp": now,
            })
            break

    if not found:
        raise HTTPException(status_code=404, detail="Ticket not found")

    _save_tickets(tickets)
    return found


@router.patch("/{ticket_id}/assign")
async def assign_agent(ticket_id: str, payload: AssignPayload):
    tickets = _load_tickets()
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    msg_id = f"msg-assign-{int(datetime.datetime.now().timestamp() * 1000)}"

    found = None
    for t in tickets:
        if t["id"] == ticket_id:
            found = t
            t["assignedTo"] = payload.agentName
            t["updatedAt"] = now
            t["messages"].append({
                "id": msg_id,
                "senderId": "system",
                "senderName": "System",
                "senderEmail": "",
                "senderRole": "admin",
                "message": f"Ticket assigned to {payload.agentName} by {payload.adminName}",
                "timestamp": now,
            })
            break

    if not found:
        raise HTTPException(status_code=404, detail="Ticket not found")

    _save_tickets(tickets)
    return found


@router.patch("/{ticket_id}/read-user")
async def mark_user_read(ticket_id: str):
    tickets = _load_tickets()
    for t in tickets:
        if t["id"] == ticket_id:
            t["unreadByUser"] = False
            break
    _save_tickets(tickets)
    return {"status": "ok"}


@router.patch("/{ticket_id}/read-admin")
async def mark_admin_read(ticket_id: str):
    tickets = _load_tickets()
    for t in tickets:
        if t["id"] == ticket_id:
            t["unreadByAdmin"] = False
            break
    _save_tickets(tickets)
    return {"status": "ok"}
