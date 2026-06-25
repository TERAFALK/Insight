import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import current_user, require_admin
from app.core import app_settings
from app.core.integration_cache import get_cached, refresh_in_background, set_cached
from app.core.security import encrypt
from app.core.time_utils import now_stockholm
from app.db.database import get_db
from app.db.models import Customer, CustomerContact, IntegrationCredential, User
from app.integrations.registry import INTEGRATIONS, get_client

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class CustomerCreate(BaseModel):
    name: str
    contact_name: str = ""
    contact_email: str
    city: str = ""


class CustomerUpdate(BaseModel):
    name: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    city: str | None = None


class ContactCreate(BaseModel):
    name: str
    email: str
    phone: str = ""
    title: str = ""


class ContactUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    title: str | None = None


class CredentialUpsert(BaseModel):
    api_key: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


# ── Kund-CRUD ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_customers(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    q = select(Customer).where(Customer.is_active == True).options(selectinload(Customer.credentials)).order_by(Customer.name).offset(skip).limit(limit)
    if user.role == "customer":
        q = q.where(Customer.id == user.customer_id)
    rows = await db.scalars(q)
    result = []
    for c in rows.all():
        verified = {cr.integration_type for cr in c.credentials if cr.is_verified}
        configured = {cr.integration_type for cr in c.credentials}
        result.append({
            "id": c.id,
            "name": c.name,
            "contact_name": c.contact_name,
            "contact_email": c.contact_email,
            "city": c.city,
            "integrations_configured": list(configured),
            "integrations_verified": list(verified),
        })
    return result


@router.post("", status_code=201)
async def create_customer(
    body: CustomerCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    customer = Customer(**body.model_dump())
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return {"id": customer.id, "name": customer.name}


@router.get("/integrations/available")
async def list_available_integrations(_: User = Depends(current_user)):
    """Vilka integrationstyper som finns i systemet, oavsett kund."""
    return [
        {"key": k, "display_name": m.display_name, "icon": m.icon, "description": m.description}
        for k, m in INTEGRATIONS.items()
    ]


@router.get("/{customer_id}")
async def get_customer(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if user.role == "customer" and user.customer_id != customer_id:
        raise HTTPException(403, "Åtkomst nekad")
    c = await db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .options(selectinload(Customer.credentials), selectinload(Customer.reports))
    )
    if not c:
        raise HTTPException(404, "Kund hittades inte")

    integrations_status = []
    cred_by_type = {cr.integration_type: cr for cr in c.credentials}
    for key, meta in INTEGRATIONS.items():
        cred = cred_by_type.get(key)
        integrations_status.append({
            "key": key,
            "display_name": meta.display_name,
            "icon": meta.icon,
            "configured": cred is not None,
            "verified": cred.is_verified if cred else False,
            "last_verified_at": cred.last_verified_at if cred else None,
        })

    return {
        "id": c.id,
        "name": c.name,
        "contact_name": c.contact_name,
        "contact_email": c.contact_email,
        "city": c.city,
        "integrations": integrations_status,
        "recent_reports": [
            {
                "id": r.id,
                "period": r.period,
                "status": r.send_status,
                "sent_at": r.sent_at,
                "has_pdf": bool(r.pdf_path),
            }
            for r in sorted(c.reports, key=lambda r: r.created_at, reverse=True)[:12]
        ],
    }


@router.put("/{customer_id}")
async def update_customer(
    customer_id: str,
    body: CustomerUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    c = await db.get(Customer, customer_id)
    if not c:
        raise HTTPException(404, "Kund hittades inte")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(c, field, value)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "name": c.name}


@router.delete("/{customer_id}", status_code=204)
async def delete_customer(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    c = await db.get(Customer, customer_id)
    if not c:
        raise HTTPException(404, "Kund hittades inte")
    c.is_active = False
    await db.commit()


# ── Kontaktpersoner ──────────────────────────────────────────────────────────

@router.get("/{customer_id}/contacts")
async def list_contacts(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if user.role == "customer" and user.customer_id != customer_id:
        raise HTTPException(403, "Åtkomst nekad")
    contacts = await db.scalars(
        select(CustomerContact)
        .where(CustomerContact.customer_id == customer_id, CustomerContact.is_active == True)
        .order_by(CustomerContact.name)
    )
    return [
        {"id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "title": c.title}
        for c in contacts.all()
    ]


@router.post("/{customer_id}/contacts", status_code=201)
async def create_contact(
    customer_id: str,
    body: ContactCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    c = CustomerContact(customer_id=customer_id, **body.model_dump())
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "title": c.title}


@router.put("/{customer_id}/contacts/{contact_id}")
async def update_contact(
    customer_id: str,
    contact_id: str,
    body: ContactUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    c = await db.get(CustomerContact, contact_id)
    if not c or c.customer_id != customer_id:
        raise HTTPException(404, "Kontaktperson hittades inte")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(c, field, value)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "title": c.title}


@router.delete("/{customer_id}/contacts/{contact_id}", status_code=204)
async def delete_contact(
    customer_id: str,
    contact_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    c = await db.get(CustomerContact, contact_id)
    if not c or c.customer_id != customer_id:
        raise HTTPException(404, "Kontaktperson hittades inte")
    c.is_active = False
    await db.commit()


# ── Integrations-endpoints ──────────────────────────────────────────────────

@router.put("/{customer_id}/credentials/{integration_type}")
async def upsert_credential(
    customer_id: str,
    integration_type: str,
    body: CredentialUpsert,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
):
    if integration_type not in INTEGRATIONS:
        raise HTTPException(400, f"Okänd integrationstyp: {integration_type}")

    cred = await db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.customer_id == customer_id,
            IntegrationCredential.integration_type == integration_type,
        )
    )
    if not cred:
        cred = IntegrationCredential(customer_id=customer_id, integration_type=integration_type)
        db.add(cred)

    # Ny credentials innebär att tidigare verifiering inte längre gäller
    cred.is_verified = False

    if body.api_key is not None:
        cred.api_key = encrypt(body.api_key)
    if body.tenant_id is not None:
        cred.tenant_id = encrypt(body.tenant_id)
    if body.client_id is not None:
        cred.client_id = encrypt(body.client_id)
    if body.client_secret is not None:
        cred.client_secret = encrypt(body.client_secret)

    await db.commit()
    return {"status": "ok", "integration_type": integration_type}


@router.delete("/{customer_id}/credentials/{integration_type}", status_code=204)
async def delete_credential(
    customer_id: str,
    integration_type: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
):
    cred = await db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.customer_id == customer_id,
            IntegrationCredential.integration_type == integration_type,
        )
    )
    if cred:
        await db.delete(cred)
        await db.commit()


@router.post("/{customer_id}/credentials/{integration_type}/verify")
async def verify_credential(
    customer_id: str,
    integration_type: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
):
    """
    Testar att sparade credentials för en integration faktiskt fungerar.
    Funkar identiskt för alla integrationstyper via det gemensamma gränssnittet.
    """
    if integration_type not in INTEGRATIONS:
        raise HTTPException(400, f"Okänd integrationstyp: {integration_type}")

    cred = await db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.customer_id == customer_id,
            IntegrationCredential.integration_type == integration_type,
        )
    )
    if not cred:
        raise HTTPException(400, f"Inga {integration_type}-credentials sparade för denna kund")

    client = get_client(integration_type)
    ok, message = await client.verify(cred)

    if ok:
        cred.is_verified = True
        cred.last_verified_at = now_stockholm()
        await db.commit()

    return {"status": "ok" if ok else "error", "detail": message}



@router.get("/{customer_id}/integrations/microsoft/consent-url")
async def microsoft_consent_url(
    customer_id: str,
    _: User = Depends(require_admin),
):
    """Genererar admin-consent URL för TERAFALK:s multi-tenant app."""
    if not app_settings.get("ms_app_client_id") or not app_settings.get("ms_app_redirect_uri"):
        raise HTTPException(400, "MS_APP_CLIENT_ID och MS_APP_REDIRECT_URI måste konfigureras under Inställningar")
    url = (
        f"https://login.microsoftonline.com/common/adminconsent"
        f"?client_id={app_settings.get('ms_app_client_id')}"
        f"&redirect_uri={app_settings.get('ms_app_redirect_uri')}"
        f"&state={customer_id}"
    )
    return {"url": url}


@router.get("/{customer_id}/integrations/{integration_type}/live")
async def get_integration_live_data(
    customer_id: str,
    integration_type: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
):
    """
    Returnerar cachad integration-data direkt om tillgänglig.
    Om cachen är gammal (>5 min) triggas en bakgrundsuppdatering
    medan gammal data ändå returneras omedelbart.
    Första anropet hämtar live (ingen cache ännu).
    """
    if integration_type not in INTEGRATIONS:
        raise HTTPException(400, f"Okänd integrationstyp: {integration_type}")

    cred = await db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.customer_id == customer_id,
            IntegrationCredential.integration_type == integration_type,
        )
    )
    if not cred:
        raise HTTPException(400, f"Ingen {integration_type}-integration konfigurerad")

    client = get_client(integration_type)

    async def fetch():
        return await client.fetch_report_data(cred)

    entry = get_cached(customer_id, integration_type)

    if entry is None:
        # Första anropet — hämta live och fyll cachen
        try:
            data = await fetch()
        except NotImplementedError as e:
            raise HTTPException(501, str(e))
        except Exception as e:
            raise HTTPException(502, f"Kunde inte hämta data: {e}")
        set_cached(customer_id, integration_type, data)
        return data

    # Cache finns — returnera direkt
    if entry.is_stale() and not entry.refreshing:
        asyncio.create_task(refresh_in_background(customer_id, integration_type, fetch))

    return entry.data
