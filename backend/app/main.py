"""Insight — Backend (FastAPI)"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.limiter import limiter
from app.db.database import init_db
from app.db.seed import seed_first_admin
from app.api import auth, customers, reports, integrations, scheduler as scheduler_router, users, ms_auth, admin_settings, dashboard, orders, phase_templates
from app.core import app_settings
from app.core.scheduler import start_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_first_admin()
    await app_settings.load_from_db()
    start_scheduler()
    from app.core.scheduler import reschedule_from_db
    await reschedule_from_db()
    yield


app = FastAPI(
    title="Insight",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,         prefix="/api/auth",         tags=["Auth"])
app.include_router(customers.router,    prefix="/api/customers",    tags=["Customers"])
app.include_router(reports.router,      prefix="/api/reports",      tags=["Reports"])
app.include_router(integrations.router, prefix="/api/integrations", tags=["Integrations"])
app.include_router(scheduler_router.router, prefix="/api/scheduler", tags=["Scheduler"])
app.include_router(users.router,            prefix="/api/users",     tags=["Users"])
app.include_router(ms_auth.router,          prefix="/api/auth/microsoft",  tags=["Microsoft Auth"])
app.include_router(admin_settings.router,   prefix="/api/admin/settings",  tags=["Admin Settings"])
app.include_router(dashboard.router,        prefix="/api/dashboard",        tags=["Dashboard"])
app.include_router(orders.router,           prefix="/api/orders",           tags=["Orders"])
app.include_router(phase_templates.router,  prefix="/api/phase-templates",  tags=["Phase Templates"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "insight"}
