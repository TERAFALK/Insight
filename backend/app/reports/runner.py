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

_report_semaphore = asyncio.Semaphore(3)  # max 3 rapporter parallellt

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.database import AsyncSessionLocal
from app.db.models import Customer, Report
from app.graph.sender import send_report_email
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

        period = datetime.utcnow().strftime("%Y-%m")

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

        month_names = {
            "01": "januari", "02": "februari", "03": "mars", "04": "april",
            "05": "maj", "06": "juni", "07": "juli", "08": "augusti",
            "09": "september", "10": "oktober", "11": "november", "12": "december",
        }
        year, month_num = period.split("-")
        month_sv = month_names.get(month_num, month_num)
        included = ", ".join(INTEGRATIONS[k].display_name for k in sections.keys())

        try:
            await send_report_email(
                to_email=customer.contact_email,
                to_name=customer.contact_name or customer.name,
                subject=f"Nätverksrapport {month_sv} {year} — {customer.name}",
                body_html=_email_body(customer.name, month_sv, year, included),
                pdf_path=pdf_path,
                pdf_filename=f"{customer.name} - {period}.pdf",
            )
            report.send_status = "sent"
            report.sent_at = datetime.utcnow()
        except Exception as e:
            logger.error("Graph-utskick misslyckades för %s: %s", customer.name, e)
            report.send_status = "error"
            report.error_message = str(e)

        await db.commit()
        logger.info("Rapport klar för %s (%s) — sektioner: %s", customer.name, period, list(sections.keys()))


def _email_body(customer_name: str, month: str, year: str, included_integrations: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#141414">
      <div style="background:#fff;padding:24px 28px;border:1px solid #e0e9f5;border-radius:8px 8px 0 0">
        <span style="font-family:Arial,sans-serif;font-size:20px;font-weight:700;color:#141414;letter-spacing:-0.02em">TERAFALK</span>
        <span style="font-family:Arial,sans-serif;font-size:11px;font-weight:600;color:#9499A2;letter-spacing:0.06em;text-transform:uppercase;margin-left:10px">Insight</span>
      </div>
      <div style="background:#fff;padding:28px;border:1px solid #e0e9f5;border-top:none;border-radius:0 0 8px 8px">
        <p style="margin:0 0 16px">Hej {customer_name},</p>
        <p style="margin:0 0 16px">Bifogat finns er rapport för <strong>{month} {year}</strong> från TERAFALK.</p>
        <p style="margin:0 0 16px">Rapporten omfattar: <strong>{included_integrations}</strong></p>
        <p style="margin:0 0 24px">Har ni frågor är ni välkomna att kontakta oss på support@terafalk.com.</p>
        <p style="margin:0;font-size:12px;color:#666">TERAFALK AB<br>Detta är ett automatiskt utskick — svara inte på detta e-postmeddelande.</p>
      </div>
    </div>
    """
