from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.auth import current_user, require_admin
from app.db.models import User
from app.core.config import settings

router = APIRouter()


class ScheduleUpdate(BaseModel):
    day: int
    hour: int
    minute: int


@router.get("/status")
async def scheduler_status(_: User = Depends(current_user)):
    from app.core.scheduler import get_next_run
    return {
        "schedule": f"Dag {settings.REPORT_SCHEDULE_DAY} varje månad kl {settings.REPORT_SCHEDULE_HOUR:02d}:{settings.REPORT_SCHEDULE_MINUTE:02d}",
        "day": settings.REPORT_SCHEDULE_DAY,
        "hour": settings.REPORT_SCHEDULE_HOUR,
        "minute": settings.REPORT_SCHEDULE_MINUTE,
        "timezone": "Europe/Stockholm",
        "sender": settings.GRAPH_SENDER,
        "next_run": get_next_run(),
    }


@router.put("/schedule", dependencies=[Depends(require_admin)])
async def update_schedule(body: ScheduleUpdate):
    if not (1 <= body.day <= 28):
        raise HTTPException(400, "Dag måste vara mellan 1 och 28")
    if not (0 <= body.hour <= 23):
        raise HTTPException(400, "Timme måste vara mellan 0 och 23")
    if not (0 <= body.minute <= 59):
        raise HTTPException(400, "Minut måste vara mellan 0 och 59")
    from app.core.scheduler import reschedule
    reschedule(body.day, body.hour, body.minute)
    # Uppdatera settings i minnet så att /status återspeglar ny tid direkt
    settings.REPORT_SCHEDULE_DAY = body.day
    settings.REPORT_SCHEDULE_HOUR = body.hour
    settings.REPORT_SCHEDULE_MINUTE = body.minute
    return {"status": "ok", "day": body.day, "hour": body.hour, "minute": body.minute}
