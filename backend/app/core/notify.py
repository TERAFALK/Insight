"""Hjälpare för att skapa in-app-notiser (klockan i topbaren).

Loggning/notis får aldrig fälla den egentliga åtgärden — fel sväljs tyst.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notification, User

logger = logging.getLogger(__name__)


async def notify_user(
    db: AsyncSession,
    user_id: str,
    type: str,
    title: str,
    body: str = "",
    link_ticket_id: str | None = None,
    icon: str = "ti-bell",
) -> None:
    """Lägg en notis i sessionen för en specifik användare. Commit sker av anroparen."""
    try:
        db.add(Notification(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=type,
            title=title,
            body=body[:500] if body else "",
            link_ticket_id=link_ticket_id,
            icon=icon,
        ))
    except Exception as e:  # pragma: no cover
        logger.warning("Kunde inte skapa notis för %s: %s", user_id, e)


async def notify_admins(
    db: AsyncSession,
    type: str,
    title: str,
    body: str = "",
    link_ticket_id: str | None = None,
    icon: str = "ti-bell",
    exclude_user_id: str | None = None,
) -> None:
    """Notifiera alla aktiva admins (utom ev. den som utlöste händelsen)."""
    try:
        admins = (await db.scalars(
            select(User).where(User.role == "admin", User.is_active == True)
        )).all()
        for a in admins:
            if a.id == exclude_user_id:
                continue
            await notify_user(db, a.id, type, title, body, link_ticket_id, icon)
    except Exception as e:  # pragma: no cover
        logger.warning("Kunde inte notifiera admins: %s", e)
