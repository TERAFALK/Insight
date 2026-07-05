"""API-endpoints för ITIL-baserad ärendehantering."""

import html
import logging
import math
import os
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import current_user, require_admin
from app.core.limiter import limiter
from app.core.uploads import save_upload, validate_extension
from app.db.database import get_db
from app.db.models import (
    Customer, CustomerContact, Ticket, TicketAttachment,
    TicketContact, TicketHistory, TicketMessage,
    TicketTag, TicketTimeEntry, User,
)

router = APIRouter()

logger = logging.getLogger(__name__)

ATTACHMENTS_DIR = "/app/ticket_attachments"

VALID_TYPES     = {"incident", "service_request", "change", "problem", "information"}
VALID_STATUSES  = {"new", "open", "in_progress", "pending_customer", "resolved", "closed", "cancelled"}
VALID_PRIOS     = {"critical", "high", "medium", "low"}


# ── Behörighet ─────────────────────────────────────────────────────────────────

def _is_staff(user: User) -> bool:
    return user.role == "admin"


def _check_ticket_access(ticket: Ticket, user: User) -> None:
    if _is_staff(user):
        return
    if ticket.customer_id != user.customer_id:
        raise HTTPException(status_code=403, detail="Åtkomst nekad")


# ── Hjälpfunktioner ────────────────────────────────────────────────────────────

async def _generate_ticket_number(db: AsyncSession) -> str:
    from app.core.ticket_numbers import generate_ticket_number
    return await generate_ticket_number(db)


async def _sla_dues(priority: str, db: AsyncSession) -> tuple[datetime | None, datetime | None]:
    """(first_response_due, resolution_due) för prioriteten."""
    from app.core.sla import sla_due_dates
    return await sla_due_dates(db, priority)


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
            selectinload(Ticket.parent),
            selectinload(Ticket.merged_children),
            selectinload(Ticket.tags),
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
        "first_response_due_at": ticket.first_response_due_at.isoformat() if ticket.first_response_due_at else None,
        "response_sla_breached": ticket.response_sla_breached,
        "first_responded_at": ticket.first_responded_at.isoformat() if ticket.first_responded_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
        "csat_score": ticket.csat_score,
        "csat_comment": ticket.csat_comment,
        "csat_submitted_at": ticket.csat_submitted_at.isoformat() if ticket.csat_submitted_at else None,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "parent_ticket_id": ticket.parent_ticket_id,
        "parent_ticket_number": ticket.parent.ticket_number if ticket.parent else None,
        "tags": [{"id": tg.id, "name": tg.name, "color": tg.color} for tg in (ticket.tags or [])],
        "merged_children": [
            {"id": c.id, "ticket_number": c.ticket_number, "title": c.title}
            for c in (ticket.merged_children or [])
        ],
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

def _ticket_list_dict(t: Ticket) -> dict:
    """Lätt summering för listvyer — laddar inte meddelanden/historik/tid."""
    return {
        "id": t.id,
        "ticket_number": t.ticket_number,
        "customer_id": t.customer_id,
        "customer_name": t.customer.name if t.customer else None,
        "type": t.type,
        "status": t.status,
        "priority": t.priority,
        "title": t.title,
        "assigned_to": {
            "id": t.assigned_to.id,
            "name": t.assigned_to.full_name or t.assigned_to.email,
        } if t.assigned_to else None,
        "sla_due_at": t.sla_due_at.isoformat() if t.sla_due_at else None,
        "sla_breached": t.sla_breached,
        "response_sla_breached": t.response_sla_breached,
        "parent_ticket_id": t.parent_ticket_id,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "tags": [{"id": tg.id, "name": tg.name, "color": tg.color} for tg in (t.tags or [])],
        "contacts": [
            {"contact_id": tc.contact_id, "email": tc.contact.email}
            for tc in (t.contacts or []) if tc.contact
        ],
    }


@router.get("")
async def list_tickets(
    status: str | None = None,
    priority: str | None = None,
    type: str | None = None,
    assigned_to: str | None = None,
    customer_id: str | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 1000,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # Lätt query — bara relationerna som listvyn behöver.
    q = select(Ticket).options(
        selectinload(Ticket.customer),
        selectinload(Ticket.assigned_to),
        selectinload(Ticket.contacts).selectinload(TicketContact.contact),
        selectinload(Ticket.tags),
    )
    if not _is_staff(user):
        q = q.where(Ticket.customer_id == user.customer_id)
    elif customer_id:
        q = q.where(Ticket.customer_id == customer_id)

    if search and search.strip():
        like = f"%{search.strip()}%"
        msg_ids = select(TicketMessage.ticket_id).where(TicketMessage.body.ilike(like))
        q = q.where(or_(
            Ticket.title.ilike(like),
            Ticket.ticket_number.ilike(like),
            Ticket.description.ilike(like),
            Ticket.customer.has(Customer.name.ilike(like)),
            Ticket.tags.any(TicketTag.name.ilike(like)),
            Ticket.id.in_(msg_ids),
        ))

    if status:
        q = q.where(Ticket.status == status)
    if priority:
        q = q.where(Ticket.priority == priority)
    if type:
        q = q.where(Ticket.type == type)
    if assigned_to:
        q = q.where(Ticket.assigned_to_user_id == assigned_to)

    q = q.order_by(Ticket.created_at.desc()).offset(skip).limit(min(limit, 2000))
    result = await db.scalars(q)
    return [_ticket_list_dict(t) for t in result.all()]


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
@limiter.limit("30/minute")
async def create_ticket(
    request: Request,
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
    response_due, resolution_due = await _sla_dues(body.priority, db)

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
        sla_due_at=resolution_due,
        first_response_due_at=response_due,
        source="portal",
    )
    db.add(ticket)
    await db.flush()

    await _add_history(db, ticket.id, user.id, "status", None, "new")

    # En kundanvändare som skapar ett ärende kopplas som kontakt/bevakare på ärendet
    if user.role == "customer":
        own_contact = await db.scalar(
            select(CustomerContact).where(
                CustomerContact.user_id == user.id,
                CustomerContact.customer_id == body.customer_id,
                CustomerContact.is_active == True,
            )
        )
        if own_contact:
            db.add(TicketContact(id=str(uuid.uuid4()), ticket_id=ticket.id, contact_id=own_contact.id))

    await db.commit()

    # Bekräftelsemejl — skickas till den kundanvändare som skapade ärendet.
    # (Personal/admin som skapar åt en kund får inget autosvar.)
    try:
        if user.role == "customer" and user.email:
            from app.graph.ticket_mailer import send_ticket_created
            await send_ticket_created(
                ticket_number, body.title, user.email, user.full_name or user.email,
                ticket_id=ticket.id,
            )
    except Exception as e:
        logger.warning("Kunde inte skicka bekräftelsemejl för %s: %s", ticket_number, e)

    ticket = await _get_ticket_or_404(ticket.id, db)
    return _ticket_dict(ticket, include_internals=_is_staff(user))


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
        # Kund får enbart markera sitt eget ärende som löst — inga andra fält.
        other_fields = any([
            body.priority, body.type, body.category_id, body.subcategory_id,
            body.assigned_to_user_id, body.resolution, body.title,
        ])
        if other_fields or (body.status is not None and body.status != "resolved"):
            raise HTTPException(status_code=403, detail="Åtkomst nekad")

    now = datetime.now(timezone.utc)
    old_status = ticket.status
    old_assignee_id = ticket.assigned_to_user_id

    # Stängda ärenden är slutgiltiga och kan inte återöppnas (av någon).
    if ticket.status == "closed" and body.status and body.status != "closed":
        raise HTTPException(status_code=409, detail="Stängda ärenden kan inte återöppnas")

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
        response_due, resolution_due = await _sla_dues(body.priority, db)
        ticket.sla_due_at = resolution_due
        # Uppdatera first-response-deadline bara om första svaret inte redan getts
        if ticket.first_responded_at is None:
            ticket.first_response_due_at = response_due
            ticket.response_sla_breached = False

    if body.type and body.type != ticket.type:
        if body.type not in VALID_TYPES:
            raise HTTPException(status_code=400, detail="Ogiltig typ")
        await _add_history(db, ticket.id, user.id, "type", ticket.type, body.type)
        ticket.type = body.type

    # model_fields_set skiljer "skickades som null" (avtilldela) från "skickades inte".
    fields_set = body.model_fields_set
    if "assigned_to_user_id" in fields_set and (body.assigned_to_user_id or None) != ticket.assigned_to_user_id:
        await _add_history(db, ticket.id, user.id, "assigned_to",
                           ticket.assigned_to_user_id, body.assigned_to_user_id or None)
        ticket.assigned_to_user_id = body.assigned_to_user_id or None

    if "category_id" in fields_set:
        ticket.category_id = body.category_id or None
    if "subcategory_id" in fields_set:
        ticket.subcategory_id = body.subcategory_id or None
    if body.resolution is not None:
        ticket.resolution = body.resolution
    if body.title is not None:
        ticket.title = body.title

    await db.commit()
    ticket = await _get_ticket_or_404(ticket_id, db)

    # Notiser — körs efter reload så relationer (assigned_to, customer, contacts) finns laddade.
    try:
        from app.graph.ticket_mailer import (
            send_ticket_assigned, send_ticket_resolved, send_ticket_status_changed,
        )
        if ticket.assigned_to_user_id != old_assignee_id and ticket.assigned_to:
            await send_ticket_assigned(ticket, ticket.assigned_to)
        if ticket.status != old_status:
            # Löst-status får ett eget, kundvänligt mejl; övriga statusbyten den generiska notisen.
            if ticket.status == "resolved":
                await send_ticket_resolved(ticket)
            else:
                await send_ticket_status_changed(ticket, old_status, ticket.status)
    except Exception as e:
        logger.warning("Kunde inte skicka ärendenotis för %s: %s", ticket.ticket_number, e)

    return _ticket_dict(ticket, include_internals=_is_staff(user))


# ── CSAT (kundnöjdhet) ──────────────────────────────────────────────────────────

class CsatBody(BaseModel):
    score: int
    comment: str | None = None


@router.post("/{ticket_id}/csat")
async def submit_csat(
    ticket_id: str,
    body: CsatBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    _check_ticket_access(ticket, user)
    if ticket.status not in ("resolved", "closed"):
        raise HTTPException(status_code=400, detail="Ärendet är inte löst än")
    if not (1 <= body.score <= 5):
        raise HTTPException(status_code=400, detail="Betyg måste vara 1–5")
    ticket.csat_score = body.score
    ticket.csat_comment = (body.comment or "").strip() or None
    ticket.csat_submitted_at = datetime.now(timezone.utc)
    await db.commit()
    ticket = await _get_ticket_or_404(ticket_id, db)
    return _ticket_dict(ticket, include_internals=_is_staff(user))


# ── Bulkåtgärder ────────────────────────────────────────────────────────────────

class BulkBody(BaseModel):
    ids: list[str]
    action: str            # "status" | "assign" | "priority"
    value: str | None = None


@router.post("/bulk")
async def bulk_update_tickets(
    body: BulkBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.action not in ("status", "assign", "priority"):
        raise HTTPException(status_code=400, detail="Ogiltig åtgärd")
    if body.action == "status" and body.value not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Ogiltig status")
    if body.action == "priority" and body.value not in VALID_PRIOS:
        raise HTTPException(status_code=400, detail="Ogiltig prioritet")

    now = datetime.now(timezone.utc)
    updated = 0
    for tid in body.ids:
        t = await db.get(Ticket, tid)
        if not t:
            continue
        if body.action == "status":
            v = body.value
            # Stängda ärenden är terminala — hoppa över återöppning
            if t.status == "closed" and v != "closed":
                continue
            if v != t.status:
                await _add_history(db, t.id, user.id, "status", t.status, v)
                t.status = v
                if v == "resolved":
                    t.resolved_at = now
                if v == "closed":
                    t.closed_at = now
                updated += 1
        elif body.action == "assign":
            newv = body.value or None
            if newv != t.assigned_to_user_id:
                await _add_history(db, t.id, user.id, "assigned_to", t.assigned_to_user_id, newv)
                t.assigned_to_user_id = newv
                updated += 1
        elif body.action == "priority":
            v = body.value
            if v != t.priority:
                await _add_history(db, t.id, user.id, "priority", t.priority, v)
                t.priority = v
                response_due, resolution_due = await _sla_dues(v, db)
                t.sla_due_at = resolution_due
                if t.first_responded_at is None:
                    t.first_response_due_at = response_due
                    t.response_sla_breached = False
                updated += 1
    await db.commit()
    return {"updated": updated}


@router.delete("/{ticket_id}", status_code=204)
async def delete_ticket(
    ticket_id: str,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    # Ta bort bilagefiler på disk så de inte blir föräldralösa
    for att in (ticket.attachments or []):
        try:
            os.remove(att.file_path)
        except OSError:
            pass
    await db.delete(ticket)
    await db.commit()


# ── Sammanslagning ───────────────────────────────────────────────────────────────

class MergeBody(BaseModel):
    target_ticket_id: str


@router.post("/{ticket_id}/merge")
async def merge_ticket(
    ticket_id: str,
    body: MergeBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Slår ihop ett ärende (källa) in i ett annat (mål) under samma kund.

    All data (meddelanden, bilagor, tid, kontakter) flyttas till målet. Källan
    stängs och kopplas som child till målet — den kan inte längre återöppnas.
    """
    if ticket_id == body.target_ticket_id:
        raise HTTPException(status_code=400, detail="Kan inte slå ihop ett ärende med sig självt")

    source = await _get_ticket_or_404(ticket_id, db)
    target = await _get_ticket_or_404(body.target_ticket_id, db)

    if source.customer_id != target.customer_id:
        raise HTTPException(status_code=400, detail="Ärendena måste tillhöra samma kund")
    if source.parent_ticket_id:
        raise HTTPException(status_code=400, detail="Ärendet är redan sammanslaget")
    if target.parent_ticket_id:
        raise HTTPException(status_code=400, detail="Målärendet är självt sammanslaget i ett annat ärende")
    if target.status == "closed":
        raise HTTPException(status_code=400, detail="Kan inte slå ihop in i ett stängt ärende")

    now = datetime.now(timezone.utc)

    # Flytta meddelanden, bilagor och tidsposter till målet
    opts = {"synchronize_session": False}
    await db.execute(update(TicketMessage).where(TicketMessage.ticket_id == source.id).values(ticket_id=target.id), execution_options=opts)
    await db.execute(update(TicketAttachment).where(TicketAttachment.ticket_id == source.id).values(ticket_id=target.id), execution_options=opts)
    await db.execute(update(TicketTimeEntry).where(TicketTimeEntry.ticket_id == source.id).values(ticket_id=target.id), execution_options=opts)

    # Flytta kontakter (bevakare) — undvik dubbletter
    target_contact_ids = {tc.contact_id for tc in (target.contacts or [])}
    for tc in (source.contacts or []):
        if tc.contact_id in target_contact_ids:
            await db.delete(tc)
        else:
            tc.ticket_id = target.id

    # Eventuella tidigare barn till källan flyttas till målet
    await db.execute(
        update(Ticket).where(Ticket.parent_ticket_id == source.id).values(parent_ticket_id=target.id),
        execution_options=opts,
    )

    # Källans inledande beskrivning ligger separat från meddelandena — flytta in den också.
    if source.description:
        db.add(TicketMessage(
            id=str(uuid.uuid4()),
            ticket_id=target.id,
            author_user_id=source.created_by_user_id,
            author_email=source.source_email,
            author_name=None,
            body=f"<em>Från sammanslaget ärende {html.escape(source.ticket_number)}:</em><br>{source.description}",
            is_internal=False,
            source=source.source,
            created_at=source.created_at,
        ))

    # Systemnotis i målet + historik i båda
    db.add(TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=target.id,
        author_user_id=user.id,
        body=f"Ärende <strong>{html.escape(source.ticket_number)}</strong> "
             f"({html.escape(source.title)}) slogs samman hit.",
        is_internal=True,
        source="portal",
    ))
    await _add_history(db, target.id, user.id, "merged_from", None, source.ticket_number)
    await _add_history(db, source.id, user.id, "merged_into", None, target.ticket_number)

    # Stäng källan och koppla som child
    source.parent_ticket_id = target.id
    source.status = "closed"
    source.closed_at = now

    await db.commit()
    target = await _get_ticket_or_404(target.id, db)
    return _ticket_dict(target, include_internals=True)


# ── Meddelanden ────────────────────────────────────────────────────────────────

class MessageBody(BaseModel):
    body: str
    is_internal: bool = False
    attachment_ids: list[str] = []


@router.post("/{ticket_id}/messages", status_code=201)
@limiter.limit("60/minute")
async def post_message(
    request: Request,
    ticket_id: str,
    body: MessageBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    _check_ticket_access(ticket, user)

    if body.is_internal and not _is_staff(user):
        raise HTTPException(status_code=403, detail="Interna noter är bara för personal")

    # Stängda ärenden är slutgiltiga — kunder kan inte återuppta dem via portalen.
    if ticket.status == "closed" and not _is_staff(user):
        raise HTTPException(status_code=409, detail="Ärendet är stängt och kan inte återöppnas. Skapa ett nytt ärende.")

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

    # Koppla ev. bifogade filer (uppladdade innan svaret) till detta meddelande
    linked_attachments = []
    for att_id in (body.attachment_ids or []):
        att = await db.get(TicketAttachment, att_id)
        if att and att.ticket_id == ticket.id and att.message_id is None:
            att.message_id = msg.id
            linked_attachments.append(att)

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
            elif ticket.status == "resolved":
                # Kund återkopplar på ett löst (men ej stängt) ärende → öppna igen
                ticket.status = "in_progress"
                ticket.resolved_at = None
                await _add_history(db, ticket.id, user.id, "status", "resolved", "in_progress")

    await db.commit()

    # Bygg mejlbilagor av de kopplade filerna (bara publika svar mejlas ut).
    # Graph inline-bilagor hålls små; större filer förblir i portalen.
    email_attachments = []
    if not body.is_internal and linked_attachments:
        import base64
        from app.graph.mailer import file_attachment
        for att in linked_attachments:
            try:
                if os.path.getsize(att.file_path) <= 3 * 1024 * 1024:
                    with open(att.file_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    email_attachments.append(file_attachment(att.original_name, b64, att.mime_type))
                else:
                    logger.info("Bilaga %s för stor för mejl — endast i portalen", att.original_name)
            except OSError:
                pass

    # Skicka notifiering
    try:
        from app.graph.ticket_mailer import send_ticket_reply
        await send_ticket_reply(ticket, user, safe_body, body.is_internal, attachments=email_attachments)
    except Exception as e:
        logger.warning("Kunde inte skicka svarsnotis för %s: %s", ticket.ticket_number, e)

    # @mentions i interna noteringar → notifiera nämnda kollegor
    if body.is_internal:
        try:
            import re
            mentioned = {e.lower() for e in re.findall(r"@([\w.+-]+@[\w.-]+\.\w+)", body.body)}
            if mentioned:
                admins = (await db.scalars(
                    select(User).where(User.role == "admin", User.is_active == True)
                )).all()
                from app.graph.ticket_mailer import send_ticket_mention
                for mu in admins:
                    if mu.email.lower() in mentioned and mu.id != user.id:
                        await send_ticket_mention(ticket, mu, safe_body, user)
        except Exception as e:
            logger.warning("Kunde inte skicka mention-notis för %s: %s", ticket.ticket_number, e)

    await db.refresh(msg)
    return {
        "id": msg.id,
        "body": msg.body,
        "is_internal": msg.is_internal,
        "created_at": msg.created_at.isoformat(),
    }


# ── Bilagor ────────────────────────────────────────────────────────────────────

@router.post("/{ticket_id}/attachments", status_code=201)
@limiter.limit("30/minute")
async def upload_attachment(
    request: Request,
    ticket_id: str,
    message_id: str | None = None,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    _check_ticket_access(ticket, user)
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

    ext = validate_extension(file.filename)
    att_id = str(uuid.uuid4())
    stored = f"{att_id}{ext}"
    file_path = os.path.join(ATTACHMENTS_DIR, stored)

    await save_upload(file, file_path)

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
    ticket = await _get_ticket_or_404(ticket_id, db)
    return _ticket_dict(ticket, include_internals=True)


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


# ── Taggar ─────────────────────────────────────────────────────────────────────

class AddTagBody(BaseModel):
    tag_id: str


@router.post("/{ticket_id}/tags", status_code=201)
async def add_ticket_tag(
    ticket_id: str,
    body: AddTagBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _is_staff(user):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")
    from app.db.models import TicketTag, TicketTagLink
    ticket = await _get_ticket_or_404(ticket_id, db)
    tag = await db.get(TicketTag, body.tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tagg hittades inte")
    exists = await db.scalar(
        select(TicketTagLink).where(
            TicketTagLink.ticket_id == ticket.id, TicketTagLink.tag_id == body.tag_id
        )
    )
    if not exists:
        db.add(TicketTagLink(ticket_id=ticket.id, tag_id=body.tag_id))
        await db.commit()
    ticket = await _get_ticket_or_404(ticket_id, db)
    return _ticket_dict(ticket, include_internals=True)


@router.delete("/{ticket_id}/tags/{tag_id}", status_code=204)
async def remove_ticket_tag(
    ticket_id: str,
    tag_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _is_staff(user):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")
    from app.db.models import TicketTagLink
    from sqlalchemy import delete as sqldelete
    await db.execute(
        sqldelete(TicketTagLink).where(
            TicketTagLink.ticket_id == ticket_id, TicketTagLink.tag_id == tag_id
        )
    )
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
