"""Tjänstekatalog och kundtilldelning av tjänster (t.ex. Managed Network)."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import current_user, require_admin
from app.core.audit import log_action
from app.db.database import get_db
from app.db.models import Customer, CustomerService, Service, User
from app.integrations.registry import INTEGRATIONS

router = APIRouter()

_VALID_STATUS = ("active", "paused", "ended")


def _norm_integration(value: str | None) -> str | None:
    """Tom sträng → None. Validera mot kända integrationstyper."""
    if not value:
        return None
    if value not in INTEGRATIONS:
        raise HTTPException(400, f"Okänd integrationstyp: {value}")
    return value


# ── Serialisering ─────────────────────────────────────────────────────────────

def _service_dict(s: Service) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "icon": s.icon,
        "color": s.color,
        "monthly_price": s.monthly_price,
        "integration_type": s.integration_type,
        "position": s.position,
        "is_active": s.is_active,
    }


def _assignment_dict(cs: CustomerService) -> dict:
    svc = cs.service
    catalog_price = svc.monthly_price if svc else 0
    return {
        "id": cs.id,
        "service_id": cs.service_id,
        "status": cs.status,
        "start_date": cs.start_date.isoformat() if cs.start_date else None,
        "notes": cs.notes,
        "price": cs.price,                              # överskrivning (kan vara None)
        "effective_price": cs.price if cs.price is not None else catalog_price,
        "catalog_price": catalog_price,
        "name": svc.name if svc else "—",
        "icon": svc.icon if svc else "ti-shield-check",
        "color": svc.color if svc else "#0047A3",
        "description": svc.description if svc else "",
        "integration_type": svc.integration_type if svc else None,
    }


# ── Katalog-CRUD ──────────────────────────────────────────────────────────────

@router.get("")
async def list_services(
    include_inactive: bool = False,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Service).order_by(Service.position, Service.name)
    if not include_inactive:
        q = q.where(Service.is_active == True)
    rows = await db.scalars(q)
    return [_service_dict(s) for s in rows.all()]


class ServiceBody(BaseModel):
    name: str
    description: str = ""
    icon: str = "ti-shield-check"
    color: str = "#0047A3"
    monthly_price: int = 0
    integration_type: str | None = None
    position: int = 0
    is_active: bool = True


@router.post("", status_code=201)
async def create_service(
    body: ServiceBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Namn krävs")
    itype = _norm_integration(body.integration_type)
    exists = await db.scalar(select(Service).where(Service.name == name))
    if exists:
        # En tidigare borttagen (inaktiverad) tjänst med samma namn återaktiveras
        # och uppdateras — annars blockerar unik-nyckeln att man skapar den igen.
        if exists.is_active:
            raise HTTPException(400, "En tjänst med det namnet finns redan")
        exists.description = body.description
        exists.icon = body.icon
        exists.color = body.color
        exists.monthly_price = body.monthly_price
        exists.integration_type = itype
        exists.is_active = True
        await log_action(db, admin, "service.create", "service", exists.id, f"Återaktiverade tjänst {name}")
        await db.commit()
        await db.refresh(exists)
        return _service_dict(exists)
    s = Service(**body.model_dump())
    s.name = name
    s.integration_type = itype
    db.add(s)
    await db.flush()
    await log_action(db, admin, "service.create", "service", s.id, f"Skapade tjänst {s.name}")
    await db.commit()
    await db.refresh(s)
    return _service_dict(s)


class ServiceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    monthly_price: int | None = None
    integration_type: str | None = None
    position: int | None = None
    is_active: bool | None = None


@router.put("/{service_id}")
async def update_service(
    service_id: str,
    body: ServiceUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(Service, service_id)
    if not s:
        raise HTTPException(404, "Tjänst hittades inte")
    data = body.model_dump(exclude_none=True)
    # Tillåt att nolla integrationskopplingen: hantera separat (exclude_none tar bort None/"").
    if "integration_type" in body.model_fields_set:
        s.integration_type = _norm_integration(body.integration_type)
        data.pop("integration_type", None)
    for field, value in data.items():
        setattr(s, field, value)
    await log_action(db, admin, "service.update", "service", s.id, f"Uppdaterade tjänst {s.name}")
    await db.commit()
    await db.refresh(s)
    return _service_dict(s)


@router.delete("/{service_id}", status_code=204)
async def delete_service(
    service_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(Service, service_id)
    if not s:
        raise HTTPException(404, "Tjänst hittades inte")
    # Soft delete så att befintliga kundtilldelningar behåller sitt namn/historik
    s.is_active = False
    await log_action(db, admin, "service.delete", "service", s.id, f"Inaktiverade tjänst {s.name}")
    await db.commit()


# ── Kundtilldelningar ─────────────────────────────────────────────────────────

@router.get("/customer/{customer_id}")
async def list_customer_services(
    customer_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role == "customer" and user.customer_id != customer_id:
        raise HTTPException(403, "Åtkomst nekad")
    rows = await db.scalars(
        select(CustomerService)
        .where(CustomerService.customer_id == customer_id)
        .options(selectinload(CustomerService.service))
    )
    items = rows.all()
    items.sort(key=lambda cs: (cs.status != "active", (cs.service.name if cs.service else "")))
    return [_assignment_dict(cs) for cs in items]


class AssignBody(BaseModel):
    service_id: str
    status: str = "active"
    start_date: date | None = None
    notes: str = ""
    price: int | None = None


@router.post("/customer/{customer_id}", status_code=201)
async def assign_service(
    customer_id: str,
    body: AssignBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.status not in _VALID_STATUS:
        raise HTTPException(400, "Ogiltig status")
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Kund hittades inte")
    service = await db.get(Service, body.service_id)
    if not service:
        raise HTTPException(404, "Tjänst hittades inte")
    existing = await db.scalar(
        select(CustomerService).where(
            CustomerService.customer_id == customer_id,
            CustomerService.service_id == body.service_id,
        )
    )
    if existing:
        raise HTTPException(400, "Kunden har redan denna tjänst")
    cs = CustomerService(
        customer_id=customer_id,
        service_id=body.service_id,
        status=body.status,
        start_date=body.start_date,
        notes=body.notes,
        price=body.price,
    )
    db.add(cs)
    await db.flush()
    await log_action(db, admin, "customer_service.assign", "customer", customer_id,
                     f"La till tjänst {service.name} på {customer.name}")
    await db.commit()
    cs = await db.scalar(
        select(CustomerService).where(CustomerService.id == cs.id)
        .options(selectinload(CustomerService.service))
    )
    return _assignment_dict(cs)


class AssignUpdate(BaseModel):
    status: str | None = None
    start_date: date | None = None
    notes: str | None = None
    price: int | None = None


@router.put("/customer/{customer_id}/{assignment_id}")
async def update_customer_service(
    customer_id: str,
    assignment_id: str,
    body: AssignUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    cs = await db.scalar(
        select(CustomerService).where(CustomerService.id == assignment_id)
        .options(selectinload(CustomerService.service))
    )
    if not cs or cs.customer_id != customer_id:
        raise HTTPException(404, "Tjänstetilldelning hittades inte")
    if body.status is not None and body.status not in _VALID_STATUS:
        raise HTTPException(400, "Ogiltig status")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(cs, field, value)
    await log_action(db, admin, "customer_service.update", "customer", customer_id,
                     f"Uppdaterade tjänst {cs.service.name if cs.service else ''}")
    await db.commit()
    await db.refresh(cs)
    return _assignment_dict(cs)


@router.delete("/customer/{customer_id}/{assignment_id}", status_code=204)
async def remove_customer_service(
    customer_id: str,
    assignment_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    cs = await db.scalar(
        select(CustomerService).where(CustomerService.id == assignment_id)
        .options(selectinload(CustomerService.service))
    )
    if not cs or cs.customer_id != customer_id:
        raise HTTPException(404, "Tjänstetilldelning hittades inte")
    name = cs.service.name if cs.service else ""
    await db.delete(cs)
    await log_action(db, admin, "customer_service.remove", "customer", customer_id,
                     f"Tog bort tjänst {name}")
    await db.commit()
