"""API-endpoints för order- och projekthantering."""

import logging
import math
import os
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_user, require_admin
from app.core.uploads import save_upload, validate_extension
from app.db.database import get_db
from app.db.models import CustomerContact, Order, OrderContact, OrderDocument, OrderPhaseTemplate, ProjectTask, TimeEntry, User

router = APIRouter()

logger = logging.getLogger(__name__)

DOCUMENTS_DIR = "/app/order_documents"


def _round_up_to_half_hour(total_minutes: int) -> int:
    """Avrunda uppåt till närmaste 30 minuter."""
    if total_minutes <= 0:
        return 0
    return math.ceil(total_minutes / 30) * 30


def _phase_dict(phase: OrderPhaseTemplate) -> dict:
    return {
        "id": phase.id,
        "order_type": phase.order_type,
        "name": phase.name,
        "position": phase.position,
        "is_default": phase.is_default,
    }


def _order_dict(order: Order, include_phase: bool = True) -> dict:
    return {
        "id": order.id,
        "customer_id": order.customer_id,
        "customer_name": order.customer.name if order.customer else None,
        "type": order.type,
        "title": order.title,
        "description": order.description,
        "status": order.status,
        "assigned_to": {
            "id": order.assigned_to.id,
            "name": order.assigned_to.full_name or order.assigned_to.email,
            "email": order.assigned_to.email,
        } if order.assigned_to else None,
        "contacts": [
            {"id": oc.id, "contact_id": oc.contact_id, "name": oc.contact.name, "email": oc.contact.email}
            for oc in (order.contacts or []) if oc.contact
        ],
        "created_by": order.created_by,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        "current_phase": _phase_dict(order.current_phase) if order.current_phase else None,
    }


async def _get_order_or_404(order_id: str, db: AsyncSession) -> Order:
    from sqlalchemy.orm import selectinload

    order = await db.scalar(
        select(Order)
        .options(
            selectinload(Order.customer),
            selectinload(Order.current_phase),
            selectinload(Order.documents),
            selectinload(Order.tasks),
            selectinload(Order.time_entries),
            selectinload(Order.assigned_to),
            selectinload(Order.contacts).selectinload(OrderContact.contact),
        )
        .where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order hittades inte")
    return order


def _check_customer_access(order: Order, user: User) -> None:
    if user.role == "admin":
        return
    if order.customer_id != user.customer_id:
        raise HTTPException(status_code=403, detail="Åtkomst nekad")


# ── Lista / Skapa ──────────────────────────────────────────────────────────────

@router.get("")
async def list_orders(
    skip: int = 0,
    limit: int = 1000,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload

    q = select(Order).options(
        selectinload(Order.customer),
        selectinload(Order.current_phase),
        selectinload(Order.assigned_to),
        selectinload(Order.contacts).selectinload(OrderContact.contact),
    )
    if user.role != "admin":
        q = q.where(Order.customer_id == user.customer_id)
    q = q.order_by(Order.created_at.desc()).offset(skip).limit(min(limit, 2000))
    result = await db.scalars(q)
    return [_order_dict(o) for o in result.all()]


class CreateOrderBody(BaseModel):
    customer_id: str
    type: str  # "order" | "project"
    title: str
    description: str | None = None
    current_phase_id: str | None = None


@router.post("", status_code=201)
async def create_order(
    body: CreateOrderBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.type not in ("order", "project"):
        raise HTTPException(status_code=400, detail="Typ måste vara 'order' eller 'project'")

    phase_id = body.current_phase_id
    if not phase_id:
        default_phase = await db.scalar(
            select(OrderPhaseTemplate)
            .where(OrderPhaseTemplate.order_type == body.type)
            .order_by(OrderPhaseTemplate.position)
        )
        phase_id = default_phase.id if default_phase else None

    order = Order(
        id=str(uuid.uuid4()),
        customer_id=body.customer_id,
        type=body.type,
        title=body.title,
        description=body.description,
        current_phase_id=phase_id,
        created_by=user.id,
    )
    db.add(order)
    await db.commit()
    order = await _get_order_or_404(order.id, db)

    try:
        from app.graph.order_mailer import send_order_created
        await send_order_created(order)
    except Exception as e:
        logger.warning("Kunde inte skicka order_created-notis för %s: %s", order.id, e)

    return order


# ── Detalj / Uppdatera / Ta bort ──────────────────────────────────────────────

@router.get("/{order_id}")
async def get_order(
    order_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    _check_customer_access(order, user)
    return _order_dict(order)


class UpdateOrderBody(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    assigned_to_user_id: str | None = None


@router.put("/{order_id}")
async def update_order(
    order_id: str,
    body: UpdateOrderBody,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    old_status = order.status

    if body.title is not None:
        order.title = body.title
    if body.description is not None:
        order.description = body.description
    if body.status is not None:
        order.status = body.status
    if body.assigned_to_user_id is not None:
        order.assigned_to_user_id = body.assigned_to_user_id or None

    await db.commit()
    order = await _get_order_or_404(order_id, db)

    # Skicka notiser asynkront
    try:
        from app.graph.order_mailer import send_order_status_changed
        if body.status and body.status != old_status:
            await send_order_status_changed(order, old_status, body.status)
    except Exception as e:
        logger.warning("Kunde inte skicka order_status-notis för %s: %s", order_id, e)

    return _order_dict(order)


@router.post("/{order_id}/contacts", status_code=201)
async def add_order_contact(
    order_id: str,
    body: dict,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    contact_id = body.get("contact_id")
    if not contact_id:
        raise HTTPException(status_code=400, detail="contact_id saknas")
    contact = await db.get(CustomerContact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Kontakt hittades inte")
    # Undvik duplikat
    for oc in order.contacts:
        if oc.contact_id == contact_id:
            return _order_dict(order)
    db.add(OrderContact(id=str(uuid.uuid4()), order_id=order_id, contact_id=contact_id))
    await db.commit()
    return _order_dict(await _get_order_or_404(order_id, db))


@router.delete("/{order_id}/contacts/{oc_id}", status_code=204)
async def remove_order_contact(
    order_id: str,
    oc_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    oc = await db.get(OrderContact, oc_id)
    if not oc or oc.order_id != order_id:
        raise HTTPException(status_code=404, detail="Kontakt ej funnen")
    await db.delete(oc)
    await db.commit()


@router.delete("/{order_id}", status_code=204)
async def delete_order(
    order_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    # Ta bort dokumentfiler
    for doc in order.documents:
        try:
            os.remove(doc.file_path)
        except OSError:
            pass
    await db.delete(order)
    await db.commit()


# ── Fas ───────────────────────────────────────────────────────────────────────

class SetPhaseBody(BaseModel):
    phase_id: str


@router.post("/{order_id}/phase")
async def set_phase(
    order_id: str,
    body: SetPhaseBody,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    phase = await db.get(OrderPhaseTemplate, body.phase_id)
    if not phase:
        raise HTTPException(status_code=404, detail="Fas hittades inte")
    if phase.order_type != order.type:
        raise HTTPException(status_code=400, detail="Fas tillhör fel ordertyp")
    old_phase_name = order.current_phase.name if order.current_phase else None
    order.current_phase_id = body.phase_id

    # Sätt status till completed om det är sista fasen för denna ordertyp
    last_phase = await db.scalar(
        select(OrderPhaseTemplate)
        .where(OrderPhaseTemplate.order_type == order.type)
        .order_by(OrderPhaseTemplate.position.desc())
    )
    if last_phase and last_phase.id == body.phase_id:
        order.status = "completed"
    elif order.status == "completed":
        order.status = "active"

    await db.commit()
    order = await _get_order_or_404(order_id, db)

    try:
        from app.graph.order_mailer import send_order_phase_changed
        await send_order_phase_changed(order, old_phase_name, phase.name)
    except Exception as e:
        logger.warning("Kunde inte skicka order_phase-notis för %s: %s", order_id, e)

    return _order_dict(order)


# ── Dokument ──────────────────────────────────────────────────────────────────

@router.get("/{order_id}/documents")
async def list_documents(
    order_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    _check_customer_access(order, user)
    return [
        {
            "id": d.id,
            "original_name": d.original_name,
            "mime_type": d.mime_type,
            "uploaded_at": d.uploaded_at.isoformat(),
        }
        for d in order.documents
    ]


@router.post("/{order_id}/documents", status_code=201)
async def upload_document(
    order_id: str,
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    os.makedirs(DOCUMENTS_DIR, exist_ok=True)

    ext = validate_extension(file.filename)
    doc_id = str(uuid.uuid4())
    stored_name = f"{doc_id}{ext}"
    file_path = os.path.join(DOCUMENTS_DIR, stored_name)

    await save_upload(file, file_path)

    doc = OrderDocument(
        id=doc_id,
        order_id=order.id,
        filename=stored_name,
        original_name=file.filename or stored_name,
        mime_type=file.content_type or "application/octet-stream",
        file_path=file_path,
    )
    db.add(doc)
    await db.commit()
    return {"id": doc.id, "original_name": doc.original_name, "mime_type": doc.mime_type}


@router.get("/{order_id}/documents/{doc_id}/download")
async def download_document(
    order_id: str,
    doc_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    _check_customer_access(order, user)
    doc = next((d for d in order.documents if d.id == doc_id), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument hittades inte")
    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="Fil saknas på disk")
    return FileResponse(
        path=doc.file_path,
        filename=doc.original_name,
        media_type=doc.mime_type,
    )


@router.delete("/{order_id}/documents/{doc_id}", status_code=204)
async def delete_document(
    order_id: str,
    doc_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    doc = next((d for d in order.documents if d.id == doc_id), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument hittades inte")
    try:
        os.remove(doc.file_path)
    except OSError:
        pass
    await db.delete(doc)
    await db.commit()


# ── Gantt-uppgifter (projekt) ──────────────────────────────────────────────────

class TaskBody(BaseModel):
    title: str
    start_date: date
    end_date: date
    completed: bool = False
    position: int = 0


@router.get("/{order_id}/tasks")
async def list_tasks(
    order_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    _check_customer_access(order, user)
    return [
        {
            "id": t.id,
            "title": t.title,
            "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat(),
            "completed": t.completed,
            "position": t.position,
        }
        for t in sorted(order.tasks, key=lambda x: x.position)
    ]


@router.post("/{order_id}/tasks", status_code=201)
async def create_task(
    order_id: str,
    body: TaskBody,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    if order.type != "project":
        raise HTTPException(status_code=400, detail="Gantt-uppgifter finns bara på projekt")
    task = ProjectTask(
        id=str(uuid.uuid4()),
        order_id=order.id,
        title=body.title,
        start_date=body.start_date,
        end_date=body.end_date,
        completed=body.completed,
        position=body.position,
    )
    db.add(task)
    await db.commit()
    return {
        "id": task.id,
        "title": task.title,
        "start_date": task.start_date.isoformat(),
        "end_date": task.end_date.isoformat(),
        "completed": task.completed,
        "position": task.position,
    }


@router.put("/{order_id}/tasks/{task_id}")
async def update_task(
    order_id: str,
    task_id: str,
    body: TaskBody,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    task = next((t for t in order.tasks if t.id == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Uppgift hittades inte")
    task.title = body.title
    task.start_date = body.start_date
    task.end_date = body.end_date
    task.completed = body.completed
    task.position = body.position
    await db.commit()
    return {
        "id": task.id,
        "title": task.title,
        "start_date": task.start_date.isoformat(),
        "end_date": task.end_date.isoformat(),
        "completed": task.completed,
        "position": task.position,
    }


@router.delete("/{order_id}/tasks/{task_id}", status_code=204)
async def delete_task(
    order_id: str,
    task_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    task = next((t for t in order.tasks if t.id == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Uppgift hittades inte")
    await db.delete(task)
    await db.commit()


# ── Tidsposter (projekt) ───────────────────────────────────────────────────────

class TimeEntryBody(BaseModel):
    description: str | None = None
    hours: int
    minutes: int
    worked_at: date


@router.get("/{order_id}/time-entries")
async def list_time_entries(
    order_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    _check_customer_access(order, user)
    return [
        {
            "id": e.id,
            "description": e.description,
            "hours": e.hours,
            "minutes": e.minutes,
            "billed_minutes": e.billed_minutes,
            "worked_at": e.worked_at.isoformat(),
            "created_at": e.created_at.isoformat(),
        }
        for e in sorted(order.time_entries, key=lambda x: x.worked_at)
    ]


@router.post("/{order_id}/time-entries", status_code=201)
async def create_time_entry(
    order_id: str,
    body: TimeEntryBody,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    if order.type != "project":
        raise HTTPException(status_code=400, detail="Tidsregistrering finns bara på projekt")
    total_minutes = body.hours * 60 + body.minutes
    billed = _round_up_to_half_hour(total_minutes)
    entry = TimeEntry(
        id=str(uuid.uuid4()),
        order_id=order.id,
        user_id=user.id,
        description=body.description,
        hours=body.hours,
        minutes=body.minutes,
        billed_minutes=billed,
        worked_at=body.worked_at,
    )
    db.add(entry)
    await db.commit()
    return {
        "id": entry.id,
        "description": entry.description,
        "hours": entry.hours,
        "minutes": entry.minutes,
        "billed_minutes": entry.billed_minutes,
        "worked_at": entry.worked_at.isoformat(),
        "created_at": entry.created_at.isoformat(),
    }


@router.delete("/{order_id}/time-entries/{entry_id}", status_code=204)
async def delete_time_entry(
    order_id: str,
    entry_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_order_or_404(order_id, db)
    entry = next((e for e in order.time_entries if e.id == entry_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Tidspost hittades inte")
    await db.delete(entry)
    await db.commit()
