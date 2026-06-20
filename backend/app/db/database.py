from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrera befintliga installationer som saknar de nya User-kolumnerna
        for stmt in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR NOT NULL DEFAULT 'admin'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS customer_id VARCHAR REFERENCES customers(id)",
        ]:
            await conn.execute(text(stmt))


async def get_db():
    session: AsyncSession = AsyncSessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
