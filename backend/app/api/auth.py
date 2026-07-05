import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import jwt as jose_jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

import logging

from app.core import app_settings
from app.core.limiter import limiter
from app.core.security import (
    create_access_token, create_reset_token, decode_reset_token, decode_token,
    hash_password, verify_password,
)
from app.db.database import get_db
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/token", response_model=Token)
@limiter.limit("5/minute")
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.email == form.username))
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Fel e-post eller lösenord")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inaktiv användare")
    return Token(access_token=create_access_token(user.email))


# ── Lösenordsåterställning ──────────────────────────────────────────────────────

class ForgotBody(BaseModel):
    email: str


class ResetBody(BaseModel):
    token: str
    password: str


@router.post("/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    body: ForgotBody,
    db: AsyncSession = Depends(get_db),
):
    """Skickar en återställningslänk om adressen finns. Svarar alltid ok (ingen user-enumeration)."""
    email = body.email.strip()
    user = await db.scalar(select(User).where(User.email.ilike(email))) if email else None
    base = (app_settings.get("portal_url") or "").rstrip("/")
    if user and user.is_active and base:
        try:
            token = create_reset_token(user.email)
            from app.graph.mailer import render_email, send_mail, heading, paragraph, button
            link = f"{base}/?pwreset={token}"
            content = (
                heading("Återställ ditt lösenord")
                + paragraph("Vi fick en begäran om att återställa lösenordet för ditt Insight-konto. "
                            "Klicka nedan för att välja ett nytt. Länken är giltig i 1 timme.")
                + button("Återställ lösenord", link)
                + paragraph("Begärde du inte detta kan du ignorera mejlet — inget ändras.")
            )
            html = render_email(content, footer_note="TERAFALK AB · Detta är ett automatiskt utskick.")
            await send_mail(
                user.email, user.full_name or user.email,
                "Återställ ditt lösenord — Insight", html,
                sender=app_settings.get("graph_sender") or None,
            )
        except Exception as e:
            logger.warning("Kunde inte skicka återställningsmejl: %s", e)
    elif user and user.is_active and not base:
        logger.warning("PORTAL_URL saknas — kan inte skicka återställningslänk")
    return {"status": "ok"}


@router.post("/reset-password")
@limiter.limit("10/minute")
async def reset_password(
    request: Request,
    body: ResetBody,
    db: AsyncSession = Depends(get_db),
):
    try:
        email = decode_reset_token(body.token)
    except Exception:
        raise HTTPException(status_code=400, detail="Länken är ogiltig eller har gått ut")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Lösenordet måste vara minst 8 tecken")
    user = await db.scalar(select(User).where(User.email == email))
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Länken är ogiltig")
    user.hashed_password = hash_password(body.password)
    await db.commit()
    return {"status": "ok"}


# ── Microsoft-inloggning (SSO för administratörer) ──────────────────────────────
# Återanvänder mail-appens registrering (graph_*). Entra gatekeepar vem som får
# logga in via "assignment required"; alla som når callbacken auto-provisioneras
# som admin. Lokalt lösenord finns kvar som reserv (break-glass).

def _ms_redirect_uri() -> str:
    base = (app_settings.get("portal_url") or "").rstrip("/")
    return f"{base}/api/auth/ms-login/callback"


def _ms_state() -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=10)
    return jose_jwt.encode(
        {"type": "msstate", "nonce": secrets.token_urlsafe(8), "exp": exp},
        settings.SECRET_KEY, algorithm="HS256",
    )


def _ms_check_state(state: str) -> bool:
    try:
        return jose_jwt.decode(state, settings.SECRET_KEY, algorithms=["HS256"]).get("type") == "msstate"
    except Exception:
        return False


@router.get("/ms-login")
@limiter.limit("15/minute")
async def ms_login_start(request: Request):
    """Returnerar Entra-authorize-URL som frontend redirectar till."""
    tenant = app_settings.get("graph_tenant_id")
    client_id = app_settings.get("graph_client_id")
    if not tenant or not client_id or not app_settings.get("portal_url"):
        raise HTTPException(400, "Microsoft-inloggning är inte konfigurerad (kräver Graph-app + PORTAL_URL)")
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": _ms_redirect_uri(),
        "response_mode": "query",
        "scope": "openid profile email",
        "state": _ms_state(),
        "prompt": "select_account",
    }
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{urlencode(params)}"
    return {"url": url}


@router.get("/ms-login/callback")
async def ms_login_callback(
    code: str = "",
    state: str = "",
    error: str = "",
    db: AsyncSession = Depends(get_db),
):
    base = (app_settings.get("portal_url") or "").rstrip("/")
    if error or not code or not _ms_check_state(state):
        return RedirectResponse(f"{base}/?msloginerror=1")

    tenant = app_settings.get("graph_tenant_id")
    client_id = app_settings.get("graph_client_id")
    client_secret = app_settings.get("graph_client_secret")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": _ms_redirect_uri(),
                    "scope": "openid profile email",
                },
            )
            r.raise_for_status()
            tokens = r.json()
        # id_token kommer direkt från Microsoft över TLS (bekräftad via vår secret).
        claims = jose_jwt.get_unverified_claims(tokens["id_token"])
        if claims.get("aud") != client_id:
            raise ValueError("Fel audience i id_token")
        email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip()
        name = claims.get("name") or email
        if not email:
            raise ValueError("Ingen e-post i id_token")
    except Exception as e:
        logger.warning("MS-inloggning misslyckades: %s", e)
        return RedirectResponse(f"{base}/?msloginerror=1")

    # Kräver en EXPLICIT app-roll i token. Entras "assignment required" kringgås av
    # Global Admins/privilegierade roller, så vi litar inte på den — approller kräver
    # uttrycklig tilldelning i Azure och kan inte kringgås.
    roles = claims.get("roles") or []
    if not any(r.lower() == "admin" for r in roles):
        logger.warning(
            "MS-inloggning nekad — %s har roles=%s (kräver app-rollen med Value 'admin')",
            email, roles,
        )
        return RedirectResponse(f"{base}/?msloginerror=3")

    user = await db.scalar(select(User).where(User.email.ilike(email)))
    if user and user.role != "admin":
        # E-posten tillhör en kundanvändare — höj aldrig via SSO
        logger.warning("MS-inloggning nekad — %s är inte admin", email)
        return RedirectResponse(f"{base}/?msloginerror=2")
    if not user:
        user = User(
            email=email,
            hashed_password=hash_password(secrets.token_urlsafe(24)),
            full_name=name,
            role="admin",
            is_active=True,
        )
        db.add(user)
    else:
        user.is_active = True
    await db.commit()

    token = create_access_token(user.email)
    return RedirectResponse(f"{base}/?mslogin={token}")


async def current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        email = decode_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ogiltig token")
    user = await db.scalar(select(User).where(User.email == email))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inaktiv användare")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Åtkomst nekad")
    return user


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "customer_id": user.customer_id,
    }
