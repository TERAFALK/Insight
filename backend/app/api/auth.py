from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
