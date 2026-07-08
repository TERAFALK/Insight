"""Tjänstekatalog (kategori → artiklar) och kundtilldelning av artiklar.

Struktur:
  Service (kategori, t.ex. Managed Network)
    └─ ServiceArticle (säljbar rad: Small Site / Per Device, artikelnr, cykel, MSRP)
         └─ CustomerServiceArticle (tilldelad kund: antal, startdatum, status)

En tjänst är "aktiv" för en kund när kunden har minst en aktiv artikel under den.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import current_user, require_admin
from app.core.audit import log_action
from app.db.database import get_db
from app.db.models import (
    Customer,
    CustomerServiceArticle,
    Service,
    ServiceArticle,
    User,
)

router = APIRouter()

_VALID_STATUS = ("active", "paused", "ended")


# ── Hjälpare ──────────────────────────────────────────────────────────────────

def _add_months(d: date, n: int) -> date:
    """Lägg till n månader på ett datum (klamrar dagen mot månadens längd)."""
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    # Sista giltiga dag i målmånaden
    if m == 12:
        last = 31
    else:
        last = (date(y, m + 1, 1) - date(y, m, 1)).days
    return date(y, m, min(d.day, last))


def _monthly_value(article: ServiceArticle | None, qty: int) -> int:
    """Normaliserat månadsintäkt (informativt): MSRP ÷ cykel × antal."""
    if not article or not article.billing_cycle_months:
        return 0
    return round(article.msrp / article.billing_cycle_months) * qty


def _monthly_cost(article: ServiceArticle | None, qty: int) -> int:
    """Normaliserad månadskostnad: cost ÷ cykel × antal."""
    if not article or not article.billing_cycle_months:
        return 0
    return round(article.cost / article.billing_cycle_months) * qty


def _article_dict(a: ServiceArticle) -> dict:
    return {
        "id": a.id,
        "service_id": a.service_id,
        "name": a.name,
        "article_number": a.article_number,
        "billing_cycle_months": a.billing_cycle_months,
        "msrp": a.msrp,
        "cost": a.cost,
        "position": a.position,
        "is_active": a.is_active,
    }


def _service_dict(s: Service, include_inactive_articles: bool = False) -> dict:
    arts = sorted(s.articles, key=lambda a: (a.position, a.name)) if s.articles else []
    if not include_inactive_articles:
        arts = [a for a in arts if a.is_active]
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "icon": s.icon,
        "color": s.color,
        "monitor_type": s.monitor_type,
        "position": s.position,
        "is_active": s.is_active,
        "articles": [_article_dict(a) for a in arts],
    }


def _assignment_dict(csa: CustomerServiceArticle) -> dict:
    a = csa.article
    svc = a.service if a else None
    binding_months = csa.binding_months
    binding_end = None
    if csa.start_date and binding_months:
        binding_end = _add_months(csa.start_date, binding_months).isoformat()
    return {
        "id": csa.id,
        "article_id": csa.article_id,
        "quantity": csa.quantity,
        "status": csa.status,
        "start_date": csa.start_date.isoformat() if csa.start_date else None,
        "binding_months": binding_months,
        "binding_end": binding_end,
        "notes": csa.notes,
        "monthly_value": _monthly_value(a, csa.quantity),
        "monthly_cost": _monthly_cost(a, csa.quantity),
        "monthly_profit": _monthly_value(a, csa.quantity) - _monthly_cost(a, csa.quantity),
        # Artikel-/tjänstinfo
        "article_name": a.name if a else "—",
        "article_number": a.article_number if a else "",
        "billing_cycle_months": a.billing_cycle_months if a else 1,
        "msrp": a.msrp if a else 0,
        "cost": a.cost if a else 0,
        "service_id": svc.id if svc else None,
        "service_name": svc.name if svc else "—",
        "icon": svc.icon if svc else "ti-shield-check",
        "color": svc.color if svc else "#0047A3",
    }


# ── Katalog (kategorier) ──────────────────────────────────────────────────────

@router.get("")
async def list_services(
    include_inactive: bool = False,
    _: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Service).options(selectinload(Service.articles)).order_by(Service.position, Service.name)
    if not include_inactive:
        q = q.where(Service.is_active == True)
    rows = await db.scalars(q)
    return [_service_dict(s, include_inactive_articles=include_inactive) for s in rows.all()]


_VALID_MONITOR = ("", "network", "microsoft", "web")


class ServiceBody(BaseModel):
    name: str
    description: str = ""
    icon: str = "ti-shield-check"
    color: str = "#0047A3"
    monitor_type: str = ""
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
    if body.monitor_type not in _VALID_MONITOR:
        raise HTTPException(400, "Ogiltig driftmodul")
    exists = await db.scalar(
        select(Service).where(Service.name == name).options(selectinload(Service.articles))
    )
    if exists:
        if exists.is_active:
            raise HTTPException(400, "En tjänst med det namnet finns redan")
        # Återaktivera en tidigare borttagen tjänst med samma namn
        exists.description = body.description
        exists.icon = body.icon
        exists.color = body.color
        exists.monitor_type = body.monitor_type
        exists.is_active = True
        await log_action(db, admin, "service.create", "service", exists.id, f"Återaktiverade tjänst {name}")
        await db.commit()
        await db.refresh(exists)
        return _service_dict(exists)
    s = Service(
        name=name, description=body.description, icon=body.icon, color=body.color,
        monitor_type=body.monitor_type, position=body.position, is_active=body.is_active,
    )
    db.add(s)
    await db.flush()
    await log_action(db, admin, "service.create", "service", s.id, f"Skapade tjänst {s.name}")
    await db.commit()
    s = await db.scalar(
        select(Service).where(Service.id == s.id).options(selectinload(Service.articles))
    )
    return _service_dict(s)


class ServiceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    monitor_type: str | None = None
    position: int | None = None
    is_active: bool | None = None


@router.put("/{service_id}")
async def update_service(
    service_id: str,
    body: ServiceUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    s = await db.scalar(
        select(Service).where(Service.id == service_id).options(selectinload(Service.articles))
    )
    if not s:
        raise HTTPException(404, "Tjänst hittades inte")
    if body.monitor_type is not None and body.monitor_type not in _VALID_MONITOR:
        raise HTTPException(400, "Ogiltig driftmodul")
    # monitor_type kan sättas till '' (Ingen) → hantera separat (exclude_none tar bort det inte, men "" är inte None)
    for field, value in body.model_dump(exclude_none=True).items():
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
    s.is_active = False
    await log_action(db, admin, "service.delete", "service", s.id, f"Inaktiverade tjänst {s.name}")
    await db.commit()


# ── Artiklar ──────────────────────────────────────────────────────────────────

class ArticleBody(BaseModel):
    name: str
    article_number: str = ""
    billing_cycle_months: int = 1
    msrp: int = 0
    cost: int = 0
    position: int = 0


@router.post("/{service_id}/articles", status_code=201)
async def create_article(
    service_id: str,
    body: ArticleBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = await db.get(Service, service_id)
    if not svc:
        raise HTTPException(404, "Tjänst hittades inte")
    if not body.name.strip():
        raise HTTPException(400, "Namn krävs")
    if body.billing_cycle_months < 1:
        raise HTTPException(400, "Faktureringscykel måste vara minst 1 månad")
    a = ServiceArticle(
        service_id=service_id,
        name=body.name.strip(),
        article_number=body.article_number.strip(),
        billing_cycle_months=body.billing_cycle_months,
        msrp=body.msrp,
        cost=max(0, body.cost),
        position=body.position,
    )
    db.add(a)
    await db.flush()
    await log_action(db, admin, "service_article.create", "service", service_id,
                     f"La till artikel {svc.name} – {a.name}")
    await db.commit()
    await db.refresh(a)
    return _article_dict(a)


class ArticleUpdate(BaseModel):
    name: str | None = None
    article_number: str | None = None
    billing_cycle_months: int | None = None
    msrp: int | None = None
    cost: int | None = None
    position: int | None = None
    is_active: bool | None = None


@router.put("/articles/{article_id}")
async def update_article(
    article_id: str,
    body: ArticleUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(ServiceArticle, article_id)
    if not a:
        raise HTTPException(404, "Artikel hittades inte")
    if body.billing_cycle_months is not None and body.billing_cycle_months < 1:
        raise HTTPException(400, "Faktureringscykel måste vara minst 1 månad")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(a, field, value)
    await log_action(db, admin, "service_article.update", "service", a.service_id,
                     f"Uppdaterade artikel {a.name}")
    await db.commit()
    await db.refresh(a)
    return _article_dict(a)


@router.delete("/articles/{article_id}", status_code=204)
async def delete_article(
    article_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(ServiceArticle, article_id)
    if not a:
        raise HTTPException(404, "Artikel hittades inte")
    a.is_active = False
    await log_action(db, admin, "service_article.delete", "service", a.service_id,
                     f"Inaktiverade artikel {a.name}")
    await db.commit()


# ── Kundtilldelningar (artikelbaserade) ───────────────────────────────────────

@router.get("/customer/{customer_id}")
async def list_customer_articles(
    customer_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role == "customer" and user.customer_id != customer_id:
        raise HTTPException(403, "Åtkomst nekad")
    rows = await db.scalars(
        select(CustomerServiceArticle)
        .where(CustomerServiceArticle.customer_id == customer_id)
        .options(selectinload(CustomerServiceArticle.article).selectinload(ServiceArticle.service))
    )
    items = rows.all()
    items.sort(key=lambda x: (
        x.status != "active",
        (x.article.service.name if x.article and x.article.service else ""),
        (x.article.name if x.article else ""),
    ))
    return [_assignment_dict(x) for x in items]


class AssignBody(BaseModel):
    article_id: str
    quantity: int = 1
    status: str = "active"
    start_date: date | None = None
    binding_months: int = 0
    notes: str = ""


@router.post("/customer/{customer_id}", status_code=201)
async def assign_article(
    customer_id: str,
    body: AssignBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.status not in _VALID_STATUS:
        raise HTTPException(400, "Ogiltig status")
    if body.quantity < 1:
        raise HTTPException(400, "Antal måste vara minst 1")
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Kund hittades inte")
    article = await db.get(ServiceArticle, body.article_id)
    if not article:
        raise HTTPException(404, "Artikel hittades inte")
    existing = await db.scalar(
        select(CustomerServiceArticle).where(
            CustomerServiceArticle.customer_id == customer_id,
            CustomerServiceArticle.article_id == body.article_id,
        )
    )
    if existing:
        raise HTTPException(400, "Kunden har redan denna artikel — ändra antalet istället")
    csa = CustomerServiceArticle(
        customer_id=customer_id,
        article_id=body.article_id,
        quantity=body.quantity,
        status=body.status,
        start_date=body.start_date,
        binding_months=max(0, body.binding_months),
        notes=body.notes,
    )
    db.add(csa)
    await db.flush()
    await log_action(db, admin, "customer_article.assign", "customer", customer_id,
                     f"La till {body.quantity}× {article.name} på {customer.name}")
    await db.commit()
    csa = await db.scalar(
        select(CustomerServiceArticle).where(CustomerServiceArticle.id == csa.id)
        .options(selectinload(CustomerServiceArticle.article).selectinload(ServiceArticle.service))
    )
    return _assignment_dict(csa)


class AssignUpdate(BaseModel):
    quantity: int | None = None
    status: str | None = None
    start_date: date | None = None
    binding_months: int | None = None
    notes: str | None = None


@router.put("/customer/{customer_id}/{assignment_id}")
async def update_customer_article(
    customer_id: str,
    assignment_id: str,
    body: AssignUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    csa = await db.scalar(
        select(CustomerServiceArticle).where(CustomerServiceArticle.id == assignment_id)
        .options(selectinload(CustomerServiceArticle.article).selectinload(ServiceArticle.service))
    )
    if not csa or csa.customer_id != customer_id:
        raise HTTPException(404, "Tilldelning hittades inte")
    if body.status is not None and body.status not in _VALID_STATUS:
        raise HTTPException(400, "Ogiltig status")
    if body.quantity is not None and body.quantity < 1:
        raise HTTPException(400, "Antal måste vara minst 1")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(csa, field, value)
    await log_action(db, admin, "customer_article.update", "customer", customer_id,
                     f"Uppdaterade artikel {csa.article.name if csa.article else ''}")
    await db.commit()
    await db.refresh(csa)
    return _assignment_dict(csa)


@router.delete("/customer/{customer_id}/{assignment_id}", status_code=204)
async def remove_customer_article(
    customer_id: str,
    assignment_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    csa = await db.scalar(
        select(CustomerServiceArticle).where(CustomerServiceArticle.id == assignment_id)
        .options(selectinload(CustomerServiceArticle.article))
    )
    if not csa or csa.customer_id != customer_id:
        raise HTTPException(404, "Tilldelning hittades inte")
    name = csa.article.name if csa.article else ""
    await db.delete(csa)
    await log_action(db, admin, "customer_article.remove", "customer", customer_id,
                     f"Tog bort artikel {name}")
    await db.commit()
