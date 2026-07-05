"""Testkonfiguration: färsk Postgres-schema per test + auth-hjälpare.

Kräver en Postgres via DATABASE_URL/TEST_DATABASE_URL. Rate-limitern stängs av.
Schemat skapas via Base.metadata (ej de råa init_db-migreringarna).
"""

import os

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci-only")
os.environ.setdefault("ENCRYPTION_KEY", "test-encryption-key-0123456789")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://insight:insight@localhost:5432/insight_test"),
)

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.limiter import limiter  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.db.models import User  # noqa: E402
from app.main import app  # noqa: E402

limiter.enabled = False


@pytest_asyncio.fixture
async def _schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # Släpp poolens anslutningar så nästa test (ny event-loop) får färska
    await engine.dispose()


@pytest_asyncio.fixture
async def client(_schema):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _make_user(email, password, role="admin", customer_id=None):
    async with AsyncSessionLocal() as db:
        db.add(User(
            email=email, hashed_password=hash_password(password), role=role,
            full_name=email, customer_id=customer_id, is_active=True,
        ))
        await db.commit()


async def _login(client, email, password):
    r = await client.post("/api/auth/token", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest_asyncio.fixture
async def admin_headers(client):
    await _make_user("admin@test.se", "pw12345678", "admin")
    token = await _login(client, "admin@test.se", "pw12345678")
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def customer_id(client, admin_headers):
    r = await client.post("/api/customers", json={"name": "Testkund"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest_asyncio.fixture
async def customer_headers(client, customer_id):
    await _make_user("kund@test.se", "pw12345678", "customer", customer_id=customer_id)
    token = await _login(client, "kund@test.se", "pw12345678")
    return {"Authorization": f"Bearer {token}"}
