"""
Microsoft 365 OAuth admin-consent callback.

Flöde:
  1. Frontend begär consent-URL via GET /api/customers/{id}/integrations/microsoft/consent-url
  2. Kund-admin öppnar URL, godkänner i Azure
  3. Azure redirectar till MS_APP_REDIRECT_URI med ?admin_consent=True&tenant=...&state={customer_id}
  4. Denna endpoint sparar tenant_id, markerar som verifierad, redirectar tillbaka till appen
"""

import html as html_lib

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt
from app.core.time_utils import now_stockholm
from app.db.database import get_db
from app.db.models import IntegrationCredential

router = APIRouter()


@router.get("/callback", response_class=HTMLResponse)
async def microsoft_consent_callback(
    admin_consent: str = "False",
    tenant: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Tar emot Azure admin consent-redirect och sparar tenant_id för kunden."""

    def _page(title: str, msg: str, ok: bool) -> str:
        color = "#16A34A" if ok else "#DC2626"
        icon = "✓" if ok else "✗"
        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
        <title>{title}</title>
        <style>body{{font-family:Arial,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#F6F7F9}}
        .box{{background:#fff;border-radius:12px;padding:40px 48px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:420px}}
        .icon{{font-size:42px;color:{color};margin-bottom:12px}}.title{{font-size:18px;font-weight:700;margin-bottom:8px}}
        .msg{{font-size:14px;color:#5C616B;margin-bottom:24px;line-height:1.5}}
        .btn{{background:#0047A3;color:#fff;border:none;border-radius:8px;padding:12px 28px;font-size:14px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}}
        </style></head><body><div class="box">
        <div class="icon">{icon}</div>
        <div class="title">{title}</div>
        <div class="msg">{msg}</div>
        <a class="btn" href="/" onclick="window.close();return false;">Stäng och gå tillbaka</a>
        </div></body></html>"""

    if error:
        return HTMLResponse(_page(
            "Koppling misslyckades",
            f"Azure returnerade ett fel: {error_description or error}",
            ok=False,
        ))

    if admin_consent.lower() != "true" or not tenant or not state:
        return HTMLResponse(_page(
            "Ogiltig callback",
            "Saknade parametrar i callback. Prova att koppla om från Insight.",
            ok=False,
        ))

    customer_id = state

    cred = await db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.customer_id == customer_id,
            IntegrationCredential.integration_type == "microsoft",
        )
    )
    if not cred:
        cred = IntegrationCredential(
            customer_id=customer_id,
            integration_type="microsoft",
        )
        db.add(cred)

    cred.tenant_id = encrypt(tenant)
    cred.is_verified = True
    cred.last_verified_at = now_stockholm()
    await db.commit()

    return HTMLResponse(_page(
        "Microsoft 365 kopplat!",
        f"Tenant <strong>{html_lib.escape(tenant)}</strong> är nu kopplat till kunden. Du kan stänga den här fliken.",
        ok=True,
    ))
