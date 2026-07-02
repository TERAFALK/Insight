"""Publika (inloggningsfria) endpoints — CSAT-enkät via engångstoken i länken."""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.db.database import get_db
from app.db.models import Ticket

router = APIRouter()


def _valid(ticket: Ticket | None, token: str) -> bool:
    return bool(ticket and ticket.csat_token and token
                and secrets.compare_digest(ticket.csat_token, token))


@router.get("/csat/{ticket_id}")
@limiter.limit("30/minute")
async def public_csat_info(
    request: Request,
    ticket_id: str,
    token: str = "",
    db: AsyncSession = Depends(get_db),
):
    ticket = await db.get(Ticket, ticket_id)
    if not _valid(ticket, token):
        raise HTTPException(status_code=404, detail="Ogiltig eller utgången enkätlänk")
    return {
        "ticket_number": ticket.ticket_number,
        "title": ticket.title,
        "already_rated": ticket.csat_score is not None,
        "score": ticket.csat_score,
    }


class PublicCsatBody(BaseModel):
    score: int
    comment: str | None = None


@router.post("/csat/{ticket_id}")
@limiter.limit("10/minute")
async def public_csat_submit(
    request: Request,
    ticket_id: str,
    body: PublicCsatBody,
    token: str = "",
    db: AsyncSession = Depends(get_db),
):
    ticket = await db.get(Ticket, ticket_id)
    if not _valid(ticket, token):
        raise HTTPException(status_code=404, detail="Ogiltig eller utgången enkätlänk")
    if not (1 <= body.score <= 5):
        raise HTTPException(status_code=400, detail="Betyg måste vara 1–5")
    ticket.csat_score = body.score
    ticket.csat_comment = (body.comment or "").strip() or None
    ticket.csat_submitted_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "ok"}
