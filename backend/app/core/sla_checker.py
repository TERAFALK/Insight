"""Kontrollerar SLA-brott och auto-stänger lösta ärenden — körs av schemaläggaren."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.db.models import Ticket, TicketHistory

logger = logging.getLogger(__name__)

OPEN_STATUSES = {"new", "open", "in_progress", "pending_customer"}

# Lösta ärenden stängs automatiskt efter så här många dagar utan ny aktivitet.
AUTO_CLOSE_DAYS = 7

# Fördröjd nöjdhetsenkät skickas så här länge efter att ärendet lösts.
CSAT_SURVEY_DELAY_DAYS = 1


async def check_sla_breaches() -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        from sqlalchemy.orm import selectinload
        tickets = await db.scalars(
            select(Ticket)
            .options(selectinload(Ticket.customer), selectinload(Ticket.assigned_to))
            .where(
                Ticket.status.in_(OPEN_STATUSES),
                Ticket.sla_due_at.isnot(None),
                Ticket.sla_due_at <= now,
                Ticket.sla_breached == False,  # noqa: E712
            )
        )
        breached = tickets.all()

        # Auto-eskalering: omfördela brutna ärenden till konfigurerad användare
        from app.core import app_settings
        from app.db.models import User, TicketHistory as _TH
        import uuid as _uuid
        esc_id = app_settings.get("escalation_user_id") or None
        esc_user = await db.get(User, esc_id) if esc_id else None

        for ticket in breached:
            ticket.sla_breached = True
            if esc_user and ticket.assigned_to_user_id != esc_user.id:
                db.add(_TH(
                    id=str(_uuid.uuid4()), ticket_id=ticket.id, user_id=None,
                    field_changed="assigned_to",
                    old_value=ticket.assigned_to_user_id, new_value=esc_user.id,
                ))
                ticket.assigned_to = esc_user  # sätter både FK och relation för notisen
                logger.info("Eskalerade %s till %s vid SLA-brott", ticket.ticket_number, esc_user.email)
            try:
                from app.graph.ticket_mailer import send_sla_breach_warning
                await send_sla_breach_warning(ticket, kind="resolution")
            except Exception as e:
                logger.warning("Kunde inte skicka SLA-varning för %s: %s", ticket.ticket_number, e)

        # First-response-SLA — brott om deadline passerat utan första svar
        resp_tickets = await db.scalars(
            select(Ticket)
            .options(selectinload(Ticket.customer), selectinload(Ticket.assigned_to))
            .where(
                Ticket.status.in_(OPEN_STATUSES),
                Ticket.first_responded_at.is_(None),
                Ticket.first_response_due_at.isnot(None),
                Ticket.first_response_due_at <= now,
                Ticket.response_sla_breached == False,  # noqa: E712
            )
        )
        resp_breached = resp_tickets.all()
        for ticket in resp_breached:
            ticket.response_sla_breached = True
            try:
                from app.graph.ticket_mailer import send_sla_breach_warning
                await send_sla_breach_warning(ticket, kind="response")
            except Exception as e:
                logger.warning("Kunde inte skicka svars-SLA-varning för %s: %s", ticket.ticket_number, e)

        if breached or resp_breached:
            await db.commit()
            logger.info("SLA-brott markerade: %d resolution, %d response", len(breached), len(resp_breached))


async def send_pending_csat_surveys() -> None:
    """Skickar fördröjd nöjdhetsenkät för lösta ärenden som ännu inte betygsatts."""
    import secrets
    from app.graph.mailer import portal_url
    if not portal_url():
        return  # Enkätlänken kräver portal_url — skickas när det konfigurerats

    from sqlalchemy.orm import selectinload
    from app.db.models import TicketContact
    from app.graph.ticket_mailer import _customer_recipients, send_csat_survey

    cutoff = datetime.now(timezone.utc) - timedelta(days=CSAT_SURVEY_DELAY_DAYS)
    async with AsyncSessionLocal() as db:
        tickets = (await db.scalars(
            select(Ticket)
            .options(
                selectinload(Ticket.customer),
                selectinload(Ticket.created_by),
                selectinload(Ticket.contacts).selectinload(TicketContact.contact),
            )
            .where(
                Ticket.status == "resolved",
                Ticket.resolved_at.isnot(None),
                Ticket.resolved_at <= cutoff,
                Ticket.csat_score.is_(None),
                Ticket.csat_survey_sent_at.is_(None),
            )
        )).all()

        sent = 0
        for ticket in tickets:
            recipients = _customer_recipients(ticket)
            now = datetime.now(timezone.utc)
            if not recipients:
                ticket.csat_survey_sent_at = now  # markera så vi inte försöker om och om
                continue
            if not ticket.csat_token:
                ticket.csat_token = secrets.token_urlsafe(24)
            try:
                await send_csat_survey(ticket, recipients)
                ticket.csat_survey_sent_at = now
                sent += 1
            except Exception as e:
                logger.warning("Kunde inte skicka CSAT-enkät för %s: %s", ticket.ticket_number, e)
        if tickets:
            await db.commit()
        if sent:
            logger.info("Skickade %d nöjdhetsenkäter", sent)


async def auto_close_resolved_tickets() -> None:
    """Stänger lösta ärenden som legat orörda längre än AUTO_CLOSE_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=AUTO_CLOSE_DAYS)
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        tickets = (await db.scalars(
            select(Ticket).where(
                Ticket.status == "resolved",
                Ticket.resolved_at.isnot(None),
                Ticket.resolved_at <= cutoff,
            )
        )).all()
        for ticket in tickets:
            ticket.status = "closed"
            ticket.closed_at = now
            db.add(TicketHistory(
                id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                user_id=None,
                field_changed="status",
                old_value="resolved",
                new_value="closed",
            ))
        if tickets:
            await db.commit()
            logger.info("Auto-stängde %d lösta ärenden", len(tickets))
