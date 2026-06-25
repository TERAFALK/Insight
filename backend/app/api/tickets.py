"""API-endpoints för ITIL-baserad ärendehantering."""

import html
import math
import os
import shutil
import uuid
from datetime import date, datetime, timezone, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import current_user, require_admin
from app.db.database import get_db, AsyncSessionLocal
from app.db.models import (
    Customer, CustomerContact, Ticket, TicketAttachment, TicketCategory,
    TicketContact, TicketHistory, TicketMessage, TicketSlaPolicy,
    TicketTimeEntry, User,
)

router = APIRouter()

ATTACHMENTS_DIR = "/app/ticket_attachments"

VALID_TYPES     = {"incident", "service_request", "change", "problem", "information"}
VALID_STATUSES  = {"new", "open", "in_progress", "pending_customer", "resolved", "closed", "cancelled"}
VALID_PRIOS     = {"critical", "high", "medium", "low"}


# ── Behörighet ─────────────────────────────────────────────────────────────────

def _is_staff(user: User) -> bool:
    return user.role in ("admin", "technician")


def _check_ticket_access(ticket: Ticket, user: User) -> None:
    if _is_staff(user):
        return
    if ticket.customer_id != user.customer_id:
        raise HTTPException(status_code=403, detail="Åtkomst nekad")


# ── Hjälpfunktioner ────────────────────────────────────────────────────────────

async def _generate_ticket_number(db: AsyncSession) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"TF{today}-"
    # Hämta högsta löpnummer för dagens prefix
    result = await db.scalar(
        select(sqlfunc.max(Ticket.ticket_number)).where(
            Ticket.ticket_number.like(f"{prefix}%")
        )
    )
    if result:
        last_seq = int(result.split("-")[-1])
        seq = last_seq + 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


async def _get_sla_due(priority: str, db: AsyncSession) -> datetime | None:
    policy = await db.scalar(
        select(TicketSlaPolicy).where(TicketSlaPolicy.priority == priority)
    )
    if not policy:
        return None
    from datetime import timedelta
    return datetime.now(timezone.utc) + timedelta(hours=policy.resolution_hours)


async def _add_history(
    db: AsyncSession,
    ticket_id: str,
    user_id: str | None,
    field: str,
    old: str | None,
    new: str | None,
) -> None:
    db.add(TicketHistory(
        id=str(uuid.uuid4()),
        ticket_id=ticket_id,
        user_id=user_id,
        field_changed=field,
        old_value=old,
        new_value=new,
    ))


async def _get_ticket_or_404(ticket_id: str, db: AsyncSession) -> Ticket:
    ticket = await db.scalar(
        select(Ticket)
        .options(
            selectinload(Ticket.customer),
            selectinload(Ticket.created_by),
            selectinload(Ticket.assigned_to),
            selectinload(Ticket.category),
            selectinload(Ticket.subcategory),
            selectinload(Ticket.messages).selectinload(TicketMessage.author),
            selectinload(Ticket.messages).selectinload(TicketMessage.attachments),
            selectinload(Ticket.attachments),
            selectinload(Ticket.history),
            selectinload(Ticket.contacts).selectinload(TicketContact.contact),
            selectinload(Ticket.time_entries).selectinload(TicketTimeEntry.user),
        )
        .where(Ticket.id == ticket_id)
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ärende hittades inte")
    return ticket


def _round_up_to_half_hour(total_minutes: int) -> int:
    if total_minutes <= 0:
        return 0
    return math.ceil(total_minutes / 30) * 30


def _ticket_dict(ticket: Ticket, include_internals: bool = True) -> dict:
    messages = []
    for m in (ticket.messages or []):
        if not include_internals and m.is_internal:
            continue
        messages.append({
            "id": m.id,
            "author_name": (m.author.full_name or m.author.email) if m.author else (m.author_name or m.author_email or "Okänd"),
            "author_role": m.author.role if m.author else "external",
            "body": m.body,
            "is_internal": m.is_internal,
            "source": m.source,
            "created_at": m.created_at.isoformat(),
            "attachments": [
                {"id": a.id, "original_name": a.original_name, "mime_type": a.mime_type}
                for a in (m.attachments or [])
            ],
        })

    history = [
        {
            "field": h.field_changed,
            "old_value": h.old_value,
            "new_value": h.new_value,
            "changed_at": h.changed_at.isoformat(),
            "user_id": h.user_id,
        }
        for h in (ticket.history or [])
    ]

    return {
        "id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "customer_id": ticket.customer_id,
        "customer_name": ticket.customer.name if ticket.customer else None,
        "type": ticket.type,
        "status": ticket.status,
        "priority": ticket.priority,
        "category": {"id": ticket.category.id, "name": ticket.category.name} if ticket.category else None,
        "subcategory": {"id": ticket.subcategory.id, "name": ticket.subcategory.name} if ticket.subcategory else None,
        "title": ticket.title,
        "description": ticket.description,
        "resolution": ticket.resolution,
        "source": ticket.source,
        "assigned_to": {
            "id": ticket.assigned_to.id,
            "name": ticket.assigned_to.full_name or ticket.assigned_to.email,
            "email": ticket.assigned_to.email,
        } if ticket.assigned_to else None,
        "created_by": {
            "id": ticket.created_by.id,
            "name": ticket.created_by.full_name or ticket.created_by.email,
        } if ticket.created_by else None,
        "sla_due_at": ticket.sla_due_at.isoformat() if ticket.sla_due_at else None,
        "sla_breached": ticket.sla_breached,
        "first_responded_at": ticket.first_responded_at.isoformat() if ticket.first_responded_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "messages": messages,
        "history": history,
        "contacts": [
            {
                "id": tc.id,
                "contact_id": tc.contact_id,
                "name": tc.contact.name,
                "email": tc.contact.email,
            }
            for tc in (ticket.contacts or [])
            if tc.contact
        ],
        "time_entries": [
            {
                "id": e.id,
                "description": e.description,
                "hours": e.hours,
                "minutes": e.minutes,
                "billed_minutes": e.billed_minutes,
                "worked_at": e.worked_at.isoformat(),
                "user_name": (e.user.full_name or e.user.email) if e.user else None,
                "created_at": e.created_at.isoformat(),
            }
            for e in (ticket.time_entries or [])
        ],
    }


# ── Lista ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_tickets(
    status: str | None = None,
    priority: str | None = None,
    type: str | None = None,
    assigned_to: str | None = None,
    customer_id: str | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Ticket).options(
        selectinload(Ticket.customer),
        selectinload(Ticket.assigned_to),
        selectinload(Ticket.created_by),
        selectinload(Ticket.category),
        selectinload(Ticket.subcategory),
        selectinload(Ticket.messages).selectinload(TicketMessage.author),
        selectinload(Ticket.messages).selectinload(TicketMessage.attachments),
        selectinload(Ticket.history),
        selectinload(Ticket.contacts).selectinload(TicketContact.contact),
        selectinload(Ticket.time_entries).selectinload(TicketTimeEntry.user),
    )
    if not _is_staff(user):
        q = q.where(Ticket.customer_id == user.customer_id)
    elif customer_id:
        q = q.where(Ticket.customer_id == customer_id)

    if status:
        q = q.where(Ticket.status == status)
    if priority:
        q = q.where(Ticket.priority == priority)
    if type:
        q = q.where(Ticket.type == type)
    if assigned_to:
        q = q.where(Ticket.assigned_to_user_id == assigned_to)

    q = q.order_by(Ticket.created_at.desc())
    result = await db.scalars(q)
    tickets = result.all()

    include_int = _is_staff(user)
    return [_ticket_dict(t, include_internals=include_int) for t in tickets]


# ── Skapa ──────────────────────────────────────────────────────────────────────

class CreateTicketBody(BaseModel):
    customer_id: str
    title: str
    description: str | None = None
    type: str = "incident"
    priority: str = "medium"
    category_id: str | None = None
    subcategory_id: str | None = None
    assigned_to_user_id: str | None = None


@router.post("", status_code=201)
async def create_ticket(
    body: CreateTicketBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # Kunder kan bara skapa ärenden för sin egen kund
    if not _is_staff(user) and body.customer_id != user.customer_id:
        raise HTTPException(status_code=403, detail="Åtkomst nekad")

    if body.type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"Ogiltig typ: {body.type}")
    if body.priority not in VALID_PRIOS:
        raise HTTPException(status_code=400, detail=f"Ogiltig prioritet: {body.priority}")

    ticket_number = await _generate_ticket_number(db)
    sla_due = await _get_sla_due(body.priority, db)

    ticket = Ticket(
        id=str(uuid.uuid4()),
        ticket_number=ticket_number,
        customer_id=body.customer_id,
        created_by_user_id=user.id,
        assigned_to_user_id=body.assigned_to_user_id,
        type=body.type,
        priority=body.priority,
        status="new",
        category_id=body.category_id,
        subcategory_id=body.subcategory_id,
        title=body.title,
        description=body.description,
        sla_due_at=sla_due,
        source="portal",
    )
    db.add(ticket)
    await db.flush()

    await _add_history(db, ticket.id, user.id, "status", None, "new")
    await db.commit()

    # Bekräftelsemejl till kunden
    try:
        from app.graph.ticket_mailer import send_ticket_created
        customer = await db.get(Customer, body.customer_id)
        if customer and customer.contact_email:
            await send_ticket_created(ticket_number, body.title, customer.contact_email, customer.name)
    except Exception:
        pass

    return await _get_ticket_or_404(ticket.id, db)


# ── Detalj ─────────────────────────────────────────────────────────────────────

@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    _check_ticket_access(ticket, user)
    return _ticket_dict(ticket, include_internals=_is_staff(user))


# ── Uppdatera ──────────────────────────────────────────────────────────────────

class UpdateTicketBody(BaseModel):
    status: str | None = None
    priority: str | None = None
    type: str | None = None
    category_id: str | None = None
    subcategory_id: str | None = None
    assigned_to_user_id: str | None = None
    resolution: str | None = None
    title: str | None = None


@router.put("/{ticket_id}")
async def update_ticket(
    ticket_id: str,
    body: UpdateTicketBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    _check_ticket_access(ticket, user)

    if not _is_staff(user):
        # Kund får bara stänga (resolved) sitt eget ärende
        allowed = body.status in ("resolved",) if body.status else True
        if not allowed or any([body.priority, body.type, body.category_id, body.assigned_to_user_id]):
            raise HTTPException(status_code=403, detail="Åtkomst nekad")

    now = datetime.now(timezone.utc)

    if body.status and body.status != ticket.status:
        if body.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail="Ogiltig status")
        await _add_history(db, ticket.id, user.id, "status", ticket.status, body.status)
        ticket.status = body.status
        if body.status == "resolved":
            ticket.resolved_at = now
        if body.status == "closed":
            ticket.closed_at = now

    if body.priority and body.priority != ticket.priority:
        if body.priority not in VALID_PRIOS:
            raise HTTPException(status_code=400, detail="Ogiltig prioritet")
        await _add_history(db, ticket.id, user.id, "priority", ticket.priority, body.priority)
        ticket.priority = body.priority
        ticket.sla_due_at = await _get_sla_due(body.priority, db)

    if body.type and body.type != ticket.type:
        if body.type not in VALID_TYPES:
            raise HTTPException(status_code=400, detail="Ogiltig typ")
        await _add_history(db, ticket.id, user.id, "type", ticket.type, body.type)
        ticket.type = body.type

    if body.assigned_to_user_id is not None and body.assigned_to_user_id != ticket.assigned_to_user_id:
        await _add_history(db, ticket.id, user.id, "assigned_to",
                           ticket.assigned_to_user_id, body.assigned_to_user_id)
        ticket.assigned_to_user_id = body.assigned_to_user_id or None

    if body.category_id is not None:
        ticket.category_id = body.category_id or None
    if body.subcategory_id is not None:
        ticket.subcategory_id = body.subcategory_id or None
    if body.resolution is not None:
        ticket.resolution = body.resolution
    if body.title is not None:
        ticket.title = body.title

    await db.commit()
    return await _get_ticket_or_404(ticket_id, db)


# ── Meddelanden ────────────────────────────────────────────────────────────────

class MessageBody(BaseModel):
    body: str
    is_internal: bool = False


@router.post("/{ticket_id}/messages", status_code=201)
async def post_message(
    ticket_id: str,
    body: MessageBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    _check_ticket_access(ticket, user)

    if body.is_internal and not _is_staff(user):
        raise HTTPException(status_code=403, detail="Interna noter är bara för personal")

    # Sanitera HTML (enkel escaping)
    safe_body = html.escape(body.body).replace("\n", "<br>")

    msg = TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        author_user_id=user.id,
        body=safe_body,
        is_internal=body.is_internal,
        source="portal",
    )
    db.add(msg)

    # Auto-statusövergång
    if not body.is_internal:
        now = datetime.now(timezone.utc)
        if _is_staff(user):
            # Tekniker/admin svarar → first_responded_at + pending_customer
            if ticket.first_responded_at is None:
                ticket.first_responded_at = now
            if ticket.status in ("new", "open", "in_progress"):
                old_status = ticket.status
                ticket.status = "pending_customer"
                await _add_history(db, ticket.id, user.id, "status", old_status, "pending_customer")
        else:
            # Kund svarar → öppna igen
            if ticket.status == "pending_customer":
                ticket.status = "in_progress"
                await _add_history(db, ticket.id, user.id, "status", "pending_customer", "in_progress")
            elif ticket.status == "new":
                ticket.status = "open"
                await _add_history(db, ticket.id, user.id, "status", "new", "open")

    await db.commit()

    # Skicka notifiering
    try:
        from app.graph.ticket_mailer import send_ticket_reply
        await send_ticket_reply(ticket, user, safe_body, body.is_internal)
    except Exception:
        pass

    await db.refresh(msg)
    return {
        "id": msg.id,
        "body": msg.body,
        "is_internal": msg.is_internal,
        "created_at": msg.created_at.isoformat(),
    }


# ── Bilagor ────────────────────────────────────────────────────────────────────

@router.post("/{ticket_id}/attachments", status_code=201)
async def upload_attachment(
    ticket_id: str,
    message_id: str | None = None,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    _check_ticket_access(ticket, user)
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

    att_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "")[1]
    stored = f"{att_id}{ext}"
    file_path = os.path.join(ATTACHMENTS_DIR, stored)

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    att = TicketAttachment(
        id=att_id,
        ticket_id=ticket.id,
        message_id=message_id,
        filename=stored,
        original_name=file.filename or stored,
        mime_type=file.content_type or "application/octet-stream",
        file_path=file_path,
        uploaded_by=user.id,
    )
    db.add(att)
    await db.commit()
    return {"id": att.id, "original_name": att.original_name, "mime_type": att.mime_type}


@router.get("/{ticket_id}/attachments/{att_id}/download")
async def download_attachment(
    ticket_id: str,
    att_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    _check_ticket_access(ticket, user)
    att = next((a for a in ticket.attachments if a.id == att_id), None)
    if not att or not os.path.exists(att.file_path):
        raise HTTPException(status_code=404, detail="Fil saknas")
    return FileResponse(path=att.file_path, filename=att.original_name, media_type=att.mime_type)


# ── Kontaktpersoner ────────────────────────────────────────────────────────────

class AddContactBody(BaseModel):
    contact_id: str


@router.post("/{ticket_id}/contacts", status_code=201)
async def add_ticket_contact(
    ticket_id: str,
    body: AddContactBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _is_staff(user):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")
    ticket = await _get_ticket_or_404(ticket_id, db)
    # Kontrollera att kontakten tillhör rätt kund
    contact = await db.get(CustomerContact, body.contact_id)
    if not contact or contact.customer_id != ticket.customer_id:
        raise HTTPException(status_code=400, detail="Kontaktperson tillhör inte den aktuella kunden")
    # Undvik dubbletter
    already = any(tc.contact_id == body.contact_id for tc in (ticket.contacts or []))
    if already:
        raise HTTPException(status_code=400, detail="Kontaktpersonen är redan tillagd")
    db.add(TicketContact(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        contact_id=body.contact_id,
    ))
    await db.commit()
    return await _get_ticket_or_404(ticket_id, db)


@router.delete("/{ticket_id}/contacts/{tc_id}", status_code=204)
async def remove_ticket_contact(
    ticket_id: str,
    tc_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _is_staff(user):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")
    ticket = await _get_ticket_or_404(ticket_id, db)
    tc = next((c for c in (ticket.contacts or []) if c.id == tc_id), None)
    if not tc:
        raise HTTPException(status_code=404, detail="Kontaktkoppling saknas")
    await db.delete(tc)
    await db.commit()


# ── Tidregistrering ────────────────────────────────────────────────────────────

class TicketTimeEntryBody(BaseModel):
    description: str | None = None
    hours: int
    minutes: int
    worked_at: date


@router.get("/{ticket_id}/time-entries")
async def list_ticket_time_entries(
    ticket_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    _check_ticket_access(ticket, user)
    return [
        {
            "id": e.id,
            "description": e.description,
            "hours": e.hours,
            "minutes": e.minutes,
            "billed_minutes": e.billed_minutes,
            "worked_at": e.worked_at.isoformat(),
            "user_name": (e.user.full_name or e.user.email) if e.user else None,
            "created_at": e.created_at.isoformat(),
        }
        for e in (ticket.time_entries or [])
    ]


@router.post("/{ticket_id}/time-entries", status_code=201)
async def create_ticket_time_entry(
    ticket_id: str,
    body: TicketTimeEntryBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _is_staff(user):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")
    ticket = await _get_ticket_or_404(ticket_id, db)
    total_minutes = body.hours * 60 + body.minutes
    billed = _round_up_to_half_hour(total_minutes)
    entry = TicketTimeEntry(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        user_id=user.id,
        description=body.description,
        hours=body.hours,
        minutes=body.minutes,
        billed_minutes=billed,
        worked_at=body.worked_at,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {
        "id": entry.id,
        "description": entry.description,
        "hours": entry.hours,
        "minutes": entry.minutes,
        "billed_minutes": entry.billed_minutes,
        "worked_at": entry.worked_at.isoformat(),
        "user_name": user.full_name or user.email,
        "created_at": entry.created_at.isoformat(),
    }


@router.delete("/{ticket_id}/time-entries/{entry_id}", status_code=204)
async def delete_ticket_time_entry(
    ticket_id: str,
    entry_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _is_staff(user):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")
    ticket = await _get_ticket_or_404(ticket_id, db)
    entry = next((e for e in (ticket.time_entries or []) if e.id == entry_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Tidspost saknas")
    await db.delete(entry)
    await db.commit()
