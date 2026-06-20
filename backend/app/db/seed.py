import logging

from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.db.database import AsyncSessionLocal
from app.db.models import User

logger = logging.getLogger(__name__)


async def seed_first_admin() -> None:
    async with AsyncSessionLocal() as db:
        existing = await db.scalar(select(User).where(User.email == settings.FIRST_ADMIN_EMAIL))
        if existing:
            return
        admin = User(
            email=settings.FIRST_ADMIN_EMAIL,
            hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
            role="admin",
            full_name="Admin",
        )
        db.add(admin)
        await db.commit()
        logger.info("Första admin-användare skapad: %s", settings.FIRST_ADMIN_EMAIL)
