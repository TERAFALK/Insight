"""Admin-endpoints för att läsa och uppdatera app-inställningar."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.core import app_settings
from app.core.audit import log_action
from app.db.database import get_db
from app.db.models import User

router = APIRouter()

_SECRET_PLACEHOLDER = "••••••••"


@router.get("")
async def get_settings(_: User = Depends(require_admin)):
    return app_settings.all_settings(mask_secrets=True)


@router.put("")
async def update_settings(
    body: dict,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    changed = []
    for key, value in body.items():
        if not isinstance(value, str):
            continue
        if value == _SECRET_PLACEHOLDER:
            continue  # oförändrad secret (maskerat värde)
        if value == "" and app_settings.is_secret(key):
            continue  # skriv aldrig över en secret med tomt
        await app_settings.update(key, value)
        changed.append(key)
    if changed:
        await log_action(db, admin, "settings.update", "settings", None,
                         f"Ändrade inställningar: {', '.join(changed)}")
        await db.commit()
    return app_settings.all_settings(mask_secrets=True)
