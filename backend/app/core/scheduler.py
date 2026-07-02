"""
Schemaläggare för automatiska månadsrapporter.
APScheduler kör i bakgrunden inuti FastAPI-processen.
Schema persisteras i DB och laddas vid uppstart.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings

logger = logging.getLogger(__name__)
_scheduler = AsyncIOScheduler()

_SETTING_KEYS = ("report_schedule_day", "report_schedule_hour", "report_schedule_minute")


def start_scheduler() -> None:
    from app.core.redis_client import acquire_run_lock

    async def _run_monthly_reports():
        if not await acquire_run_lock("scheduled_reports", 3600):
            return
        from app.reports.runner import run_scheduled_reports
        await run_scheduled_reports()

    async def _poll_ticket_inbox():
        if not await acquire_run_lock("ticket_inbox_poll", 90):
            return
        from app.graph.ticket_inbox import poll_support_inbox
        await poll_support_inbox()

    async def _check_sla_breaches():
        if not await acquire_run_lock("sla_checker", 600):
            return
        from app.core.sla_checker import check_sla_breaches
        await check_sla_breaches()

    async def _auto_close_resolved():
        if not await acquire_run_lock("auto_close_resolved", 3600):
            return
        from app.core.sla_checker import auto_close_resolved_tickets
        await auto_close_resolved_tickets()

    async def _send_csat_surveys():
        if not await acquire_run_lock("csat_surveys", 3600):
            return
        from app.core.sla_checker import send_pending_csat_surveys
        await send_pending_csat_surveys()

    # Körs varje dag; run_scheduled_reports avgör vilka kunder som är schemalagda idag.
    _scheduler.add_job(
        _run_monthly_reports,
        trigger=CronTrigger(
            hour=settings.REPORT_SCHEDULE_HOUR,
            minute=settings.REPORT_SCHEDULE_MINUTE,
            timezone="Europe/Stockholm",
        ),
        id="monthly_reports",
        replace_existing=True,
    )
    _scheduler.add_job(
        _poll_ticket_inbox,
        trigger="interval",
        minutes=2,
        id="ticket_inbox_poll",
        replace_existing=True,
    )
    _scheduler.add_job(
        _check_sla_breaches,
        trigger="interval",
        minutes=15,
        id="sla_checker",
        replace_existing=True,
    )
    _scheduler.add_job(
        _auto_close_resolved,
        trigger="interval",
        hours=6,
        id="auto_close_resolved",
        replace_existing=True,
    )
    _scheduler.add_job(
        _send_csat_surveys,
        trigger="interval",
        hours=3,
        id="csat_surveys",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler startad — rapporter körs dag %s kl %02d:%02d",
        settings.REPORT_SCHEDULE_DAY,
        settings.REPORT_SCHEDULE_HOUR,
        settings.REPORT_SCHEDULE_MINUTE,
    )


def get_next_run() -> str | None:
    job = _scheduler.get_job("monthly_reports")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def reschedule(day: int, hour: int, minute: int) -> None:
    job = _scheduler.get_job("monthly_reports")
    if not job:
        return
    job.reschedule(
        trigger=CronTrigger(
            hour=hour,
            minute=minute,
            timezone="Europe/Stockholm",
        )
    )
    settings.REPORT_SCHEDULE_DAY = day
    settings.REPORT_SCHEDULE_HOUR = hour
    settings.REPORT_SCHEDULE_MINUTE = minute
    logger.info("Schema uppdaterat — dag %s kl %02d:%02d", day, hour, minute)


async def save_schedule_to_db(day: int, hour: int, minute: int) -> None:
    from sqlalchemy import select
    from app.db.database import AsyncSessionLocal
    from app.db.models import SystemSetting

    async with AsyncSessionLocal() as db:
        for key, value in [
            ("report_schedule_day", str(day)),
            ("report_schedule_hour", str(hour)),
            ("report_schedule_minute", str(minute)),
        ]:
            existing = await db.get(SystemSetting, key)
            if existing:
                existing.value = value
            else:
                db.add(SystemSetting(key=key, value=value))
        await db.commit()


async def reschedule_from_db() -> None:
    """Laddas vid uppstart — applicerar sparat schema från DB om det finns."""
    from app.db.database import AsyncSessionLocal
    from app.db.models import SystemSetting

    async with AsyncSessionLocal() as db:
        rows = {
            key: await db.get(SystemSetting, key)
            for key in _SETTING_KEYS
        }

    if all(rows[k] is not None for k in _SETTING_KEYS):
        day = int(rows["report_schedule_day"].value)
        hour = int(rows["report_schedule_hour"].value)
        minute = int(rows["report_schedule_minute"].value)
        reschedule(day, hour, minute)
        logger.info("Schema laddat från DB — dag %s kl %02d:%02d", day, hour, minute)
