"""
Rapport-runner: bygger en rapport dynamiskt utifrån vilka integrationer
en kund faktiskt har konfigurerat OCH verifierat.

Designprincip: ingen integration är hårdkodad som obligatorisk. En kund
med bara Acronis får en ren backup-rapport. En kund med UniFi + Microsoft
365 får båda sektionerna. En kund utan några verifierade integrationer
får ingen rapport alls (loggas, skickas inte).
"""

import asyncio
import json
import logging
from datetime import datetime

from app.core.time_utils import now_stockholm

_report_semaphore = asyncio.Semaphore(3)  # max 3 rapporter parallellt

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core import app_settings
from app.db.database import AsyncSessionLocal
from app.db.models import Customer, CustomerContact, Report
from app.graph.mailer import heading, paragraph, pdf_attachment, render_email, send_mail
from app.integrations.registry import INTEGRATIONS, get_client
from app.reports.pdf_generator import generate_pdf

logger = logging.getLogger(__name__)


async def run_all_reports() -> None:
    async with AsyncSessionLocal() as db:
        customers = (
            await db.scalars(
                select(Customer)
                .where(Customer.is_active == True)
                .options(selectinload(Customer.credentials))
            )
        ).all()

    for customer in customers:
        try:
            await run_report_for_customer(customer.id)
        except Exception as e:
            logger.error("Rapport misslyckades för %s: %s", customer.name, e)


async def run_report_for_customer(customer_id: str) -> None:
    async with _report_semaphore:
        await _generate_report(customer_id)


async def run_scheduled_reports() -> None:
    """Körs dagligen. Genererar rapport för de kunder vars schema matchar idag."""
    import calendar
    from app.core.config import settings as _settings

    default_day = _settings.REPORT_SCHEDULE_DAY
    today = now_stockholm()
    quarter_months = {1, 4, 7, 10}

    async with AsyncSessionLocal() as db:
        customers = (
            await db.scalars(select(Customer).where(Customer.is_active == True))
        ).all()

    due = []
    for c in customers:
        freq = c.report_frequency or "monthly"
        if freq == "off":
            continue
        if freq == "quarterly" and today.month not in quarter_months:
            continue
        day = c.report_day or default_day
        # Klamra mot månadens sista dag (t.ex. dag 31 i februari → sista feb)
        last = calendar.monthrange(today.year, today.month)[1]
        if today.day != min(day, last):
            continue
        due.append(c)

    logger.info("Schemalagd rapportkörning: %d kunder att köra idag", len(due))
    for c in due:
        try:
            await run_report_for_customer(c.id)
        except Exception as e:
            logger.error("Rapport misslyckades för %s: %s", c.name, e)


async def _generate_report(customer_id: str) -> None:
    async with AsyncSessionLocal() as db:
        customer = await db.scalar(
            select(Customer)
            .where(Customer.id == customer_id)
            .options(selectinload(Customer.credentials))
        )
        if not customer:
            logger.error("Kund %s hittades inte", customer_id)
            return

        period = now_stockholm().strftime("%Y-%m")

        # Bygg rapportdata sektion för sektion — bara verifierade integrationer
        sections: dict[str, dict] = {}
        for cred in customer.credentials:
            if not cred.is_verified:
                logger.info(
                    "Hoppar över %s för %s — inte verifierad", cred.integration_type, customer.name
                )
                continue
            try:
                client = get_client(cred.integration_type)
                data = await client.fetch_report_data(cred)
                sections[cred.integration_type] = data
            except NotImplementedError:
                logger.info(
                    "Hoppar över %s för %s — integration inte implementerad än",
                    cred.integration_type, customer.name,
                )
            except Exception as e:
                logger.warning(
                    "Kunde inte hämta %s-data för %s: %s", cred.integration_type, customer.name, e
                )

        if not sections:
            logger.warning(
                "Inga verifierade integrationer med data för %s — ingen rapport genereras",
                customer.name,
            )
            return

        # Generera PDF utifrån vilka sektioner som faktiskt finns
        pdf_path = await generate_pdf(
            customer_name=customer.name,
            period=period,
            sections=sections,
            customer_id=customer_id,
        )

        report = Report(
            customer_id=customer_id,
            period=period,
            pdf_path=pdf_path,
            data_snapshot=json.dumps(sections, ensure_ascii=False, default=str),
        )
        db.add(report)
        await db.flush()

        month_display = {
            "01": "Januari", "02": "Februari", "03": "Mars", "04": "April",
            "05": "Maj", "06": "Juni", "07": "Juli", "08": "Augusti",
            "09": "September", "10": "Oktober", "11": "November", "12": "December",
        }
        year, month_num = period.split("-")
        month_cap = month_display.get(month_num, month_num)
        included = ", ".join(INTEGRATIONS[k].display_name for k in sections.keys())

        # Hämta alla rapportmottagare för kunden — kontakter markerade som rapportmottagare
        report_contacts = (await db.scalars(
            select(CustomerContact)
            .where(
                CustomerContact.customer_id == customer_id,
                CustomerContact.receives_reports == True,
                CustomerContact.is_active == True,
            )
        )).all()

        recipients = [(c.email, c.name) for c in report_contacts]

        # Ingen markerad mottagare → spara rapporten men skicka inget (kräver inte längre någon kund-mailpost)
        if not recipients:
            logger.warning(
                "Ingen rapportmottagare markerad för %s — rapporten genereras men skickas inte",
                customer.name,
            )
            report.send_status = "error"
            report.error_message = "Ingen kontakt markerad som rapportmottagare"
            await db.commit()
            return

        subject = f"IT-Rapport {month_cap} {year} — {customer.name}"
        body_html = _email_body(customer.name, month_cap, year, included)
        filename = f"{customer.name} - {period}.pdf"

        import base64
        with open(pdf_path, "rb") as f:
            pdf_b64 = base64.b64encode(f.read()).decode()
        attachment = pdf_attachment(filename, pdf_b64)
        report_sender = app_settings.get("graph_sender") or None

        any_sent = False
        last_error = None
        for to_email, to_name in recipients:
            try:
                await send_mail(
                    to_email, to_name, subject, body_html,
                    sender=report_sender,
                    attachments=[attachment],
                )
                any_sent = True
            except Exception as e:
                logger.error("Graph-utskick misslyckades för %s → %s: %s", customer.name, to_email, e)
                last_error = e

        if any_sent:
            report.send_status = "sent"
            report.sent_at = now_stockholm()
        else:
            report.send_status = "error"
            report.error_message = str(last_error)

        await db.commit()
        logger.info("Rapport klar för %s (%s) — sektioner: %s", customer.name, period, list(sections.keys()))


def _email_body(customer_name: str, month: str, year: str, included_integrations: str) -> str:
    content = (
        heading(f"IT-rapport {month} {year}")
        + paragraph(f"Hej {customer_name},")
        + paragraph(f"Bifogat finns er IT-rapport för <strong>{month} {year}</strong> från TERAFALK.")
        + paragraph(f"Rapporten omfattar: <strong>{included_integrations}</strong>")
        + paragraph("Har ni frågor är ni välkomna att kontakta oss på support@terafalk.com.")
    )
    return render_email(
        content,
        footer_note="TERAFALK AB · Detta är ett automatiskt utskick — svara inte på detta meddelande.",
    )
