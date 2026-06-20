"""
Insight — Backend
=================
FastAPI-applikation. Strukturerad för att enkelt lägga till
Microsoft 365-, Acronis- och Cloudfactory-integrationer senare.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.database import init_db
from app.db.seed import seed_first_admin
from app.api import auth, customers, reports, integrations, scheduler as scheduler_router
from app.core.scheduler import start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_first_admin()
    start_scheduler()
    yield


app = FastAPI(
    title="Insight",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Begränsa till din domän i produktion, t.ex. ["https://portal.terafalk.com"]
    allow_credentials=False,  # Vi använder Bearer-token i header, inte cookies — credentials behövs inte
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,         prefix="/api/auth",         tags=["Auth"])
app.include_router(customers.router,    prefix="/api/customers",    tags=["Customers"])
app.include_router(reports.router,      prefix="/api/reports",      tags=["Reports"])
app.include_router(integrations.router, prefix="/api/integrations", tags=["Integrations"])
app.include_router(scheduler_router.router, prefix="/api/scheduler", tags=["Scheduler"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "insight"}
