"""Domän- och webbövervakning per kund."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_user, require_admin
from app.core.audit import log_action
from app.core.domain_check import check_domain
from app.core.time_utils import now_stockholm
from app.db.database import get_db
from app.db.models import Customer, Domain, User

router = APIRouter()

_VALID_MONITOR = ("domain", "site")


def _days_until(d: date | None) -> int | None:
    if not d:
        return None
    return (d - date.today()).days


def _domain_dict(dm: Domain) -> dict:
    effective_renewal = dm.renewal_manual or dm.expiry_date
    return {
        "id": dm.id,
        "customer_id": dm.customer_id,
        "name": dm.name,
        "monitor_type": dm.monitor_type,
        "website_url": dm.website_url,
        "notes": dm.notes,
        "renewal_manual": dm.renewal_manual.isoformat() if dm.renewal_manual else None,
        "expiry_date": dm.expiry_date.isoformat() if dm.expiry_date else None,
        "effective_renewal": effective_renewal.isoformat() if effective_renewal else None,
        "days_to_renewal": _days_until(effective_renewal),
        "registrar": dm.registrar,
        "dmarc_status": dm.dmarc_status,
        "dmarc_policy": dm.dmarc_policy,
        "spf_status": dm.spf_status,
        "ssl_expiry": dm.ssl_expiry.isoformat() if dm.ssl_expiry else None,
        "days_to_ssl": _days_until(dm.ssl_expiry),
        "site_status": dm.site_status,
        "last_checked_at": dm.last_checked_at.isoformat() if dm.last_checked_at else None,
        "check_error": dm.check_error,
    }


@router.get("/customer/{customer_id}")
async def list_domains(
    customer_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role == "customer" and user.customer_id != customer_id:
        raise HTTPException(403, "Åtkomst nekad")
    rows = await db.scalars(
        select(Domain).where(Domain.customer_id == customer_id, Domain.is_active == True)
        .order_by(Domain.name)
    )
    return [_domain_dict(d) for d in rows.all()]


class DomainBody(BaseModel):
    name: str
    monitor_type: str = "domain"
    website_url: str = ""
    notes: str = ""
    renewal_manual: date | None = None


def _clean_name(name: str) -> str:
    n = name.strip().lower()
    n = n.split("://", 1)[-1]     # ta bort ev. schema
    n = n.split("/", 1)[0]        # ta bort ev. path
    return n.strip()


@router.post("/customer/{customer_id}", status_code=201)
async def create_domain(
    customer_id: str,
    body: DomainBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Kund hittades inte")
    if body.monitor_type not in _VALID_MONITOR:
        raise HTTPException(400, "Ogiltig övervakningstyp")
    name = _clean_name(body.name)
    if not name:
        raise HTTPException(400, "Domännamn krävs")
    dm = Domain(
        customer_id=customer_id,
        name=name,
        monitor_type=body.monitor_type,
        website_url=body.website_url.strip(),
        notes=body.notes,
        renewal_manual=body.renewal_manual,
    )
    db.add(dm)
    await db.flush()
    await log_action(db, admin, "domain.create", "customer", customer_id, f"La till domän {name}")
    await db.commit()
    await db.refresh(dm)
    return _domain_dict(dm)


class DomainUpdate(BaseModel):
    name: str | None = None
    monitor_type: str | None = None
    website_url: str | None = None
    notes: str | None = None
    renewal_manual: date | None = None


@router.put("/{domain_id}")
async def update_domain(
    domain_id: str,
    body: DomainUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    dm = await db.get(Domain, domain_id)
    if not dm:
        raise HTTPException(404, "Domän hittades inte")
    if body.monitor_type is not None and body.monitor_type not in _VALID_MONITOR:
        raise HTTPException(400, "Ogiltig övervakningstyp")
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = _clean_name(data["name"])
    for field, value in data.items():
        setattr(dm, field, value)
    await log_action(db, admin, "domain.update", "customer", dm.customer_id, f"Uppdaterade domän {dm.name}")
    await db.commit()
    await db.refresh(dm)
    return _domain_dict(dm)


@router.delete("/{domain_id}", status_code=204)
async def delete_domain(
    domain_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    dm = await db.get(Domain, domain_id)
    if not dm:
        raise HTTPException(404, "Domän hittades inte")
    dm.is_active = False
    await log_action(db, admin, "domain.delete", "customer", dm.customer_id, f"Tog bort domän {dm.name}")
    await db.commit()


async def _run_check(dm: Domain, db: AsyncSession) -> None:
    res = await check_domain(dm.name, dm.monitor_type, dm.website_url)
    dm.expiry_date = res["expiry_date"]
    dm.registrar = res["registrar"] or ""
    dm.dmarc_status = res["dmarc_status"]
    dm.dmarc_policy = res["dmarc_policy"]
    dm.spf_status = res["spf_status"]
    dm.ssl_expiry = res["ssl_expiry"]
    dm.site_status = res["site_status"]
    dm.check_error = res["check_error"]
    dm.last_checked_at = now_stockholm()


@router.post("/{domain_id}/check")
async def check_single(
    domain_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    dm = await db.get(Domain, domain_id)
    if not dm:
        raise HTTPException(404, "Domän hittades inte")
    await _run_check(dm, db)
    await db.commit()
    await db.refresh(dm)
    return _domain_dict(dm)


@router.post("/customer/{customer_id}/check-all")
async def check_all(
    customer_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(Domain).where(Domain.customer_id == customer_id, Domain.is_active == True)
    )
    domains = rows.all()
    for dm in domains:
        try:
            await _run_check(dm, db)
        except Exception:
            dm.check_error = "Kontroll misslyckades"
            dm.last_checked_at = now_stockholm()
    await db.commit()
    return [_domain_dict(dm) for dm in sorted(domains, key=lambda d: d.name)]
