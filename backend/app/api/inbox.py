"""In-app-notiser (klockan i topbaren) — lista, oläst-antal, markera läst."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_user
from app.db.database import get_db
from app.db.models import Notification, User

router = APIRouter()


def _notif_dict(n: Notification) -> dict:
    return {
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "icon": n.icon,
        "link_ticket_id": n.link_ticket_id,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("")
async def list_notifications(
    limit: int = 30,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(min(limit, 100))
    )
    items = rows.all()
    unread = await db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user.id, Notification.is_read == False
        )
    ) or 0
    return {"unread": int(unread), "items": [_notif_dict(n) for n in items]}


@router.get("/unread-count")
async def unread_count(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    unread = await db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user.id, Notification.is_read == False
        )
    ) or 0
    return {"unread": int(unread)}


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    n = await db.get(Notification, notification_id)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Notis hittades inte")
    n.is_read = True
    await db.commit()
    return {"status": "ok"}


@router.post("/read-all")
async def mark_all_read(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return {"status": "ok"}
