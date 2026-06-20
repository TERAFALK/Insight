"""
Rapport-runner: bygger en rapport dynamiskt utifrån vilka integrationer
en kund faktiskt har konfigurerat OCH verifierat.

Designprincip: ingen integration är hårdkodad som obligatorisk. En kund
med bara Acronis får en ren backup-rapport. En kund med UniFi + Microsoft
365 får båda sektionerna. En kund utan några verifierade integrationer
får ingen rapport alls (loggas, skickas inte).
"""

import json
import logging
from datetime import datetime

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
    async with AsyncSessionLocal() as db:
        customer = await db.scalar(
            select(Customer)
            .where(Customer.id == customer_id)
            .options(selectinload(Customer.credentials))
        )
        if not customer:
            logger.error("Kund %s hittades inte", customer_id)
            return

        period = datetime.now(timezone.utc).strftime("%Y-%m")

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
                pdf_filename=f"terafalk-rapport-{period}-{customer.name.lower().replace(' ', '-')}.pdf",
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
        <svg viewBox="0 0 695.39 84.24" width="160" height="19" xmlns="http://www.w3.org/2000/svg">
          <path fill="#141414" d="M236.18,455.57v15H201.74v69h-15v-69H152.3v-15Z" transform="translate(-152.3 -455.45)"/>
          <path fill="#141414" d="M263.3,478.13v7.8h54v15h-54v15.84a7.74,7.74,0,0,0,7.68,7.68H332.3v15H271a22.63,22.63,0,0,1-22.56-22.67V478.13A22.64,22.64,0,0,1,271,455.45H332.3v15H271A7.73,7.73,0,0,0,263.3,478.13Z" transform="translate(-152.3 -455.45)"/>
          <path fill="#141414" d="M412.1,524.69l7.68,15H403l-7.68-15-8-15.72-.36-.72a14.87,14.87,0,0,0-12.72-7.2h-15v38.63h-15v-84h53a22.53,22.53,0,0,1,22.56,22.56,22.75,22.75,0,0,1-13.2,20.64,20,20,0,0,1-6.48,1.8Zm-14.88-38.64a7,7,0,0,0,3.12-.72,7.62,7.62,0,0,0,4.56-7,7.92,7.92,0,0,0-2.28-5.52,7.56,7.56,0,0,0-5.4-2.16h-38v15.48Z" transform="translate(-152.3 -455.45)"/>
          <path fill="#141414" d="M512.78,539.44H496l-7.68-15-18.36-36-18.36,36-7.68,15H427.1l7.68-15,35.16-69,35.16,69Z" transform="translate(-152.3 -455.45)"/>
          <path fill="#141414" d="M604.1,455.45v15H542.78a7.73,7.73,0,0,0-7.68,7.68v7.8h54v15H535v38.51H520.1V478.13a22.64,22.64,0,0,1,22.56-22.68Z" transform="translate(-152.3 -455.45)"/>
          <path fill="#141414" d="M678.38,539.44h-16.8l-7.68-15-18.36-36-18.36,36-7.68,15H592.7l7.68-15,35.16-69,35.16,69Z" transform="translate(-152.3 -455.45)"/>
          <path fill="#141414" d="M751.7,524.57v15H709.1a26.09,26.09,0,0,1-11.76-2.76,26.59,26.59,0,0,1-12.12-12.23,26.12,26.12,0,0,1-2.76-11.76V455.57h15v58.68a12,12,0,0,0,10.2,10.2Z" transform="translate(-152.3 -455.45)"/>
          <path fill="#141414" d="M811,489.17l36.35,50.27H828.85l-29-40.07-21.24,19.32v20.75h-15v-84h15v43l12.36-11.28,11.16-10.2,23.39-21.48h22.2Z" transform="translate(-152.3 -455.45)"/>
        </svg>
      </div>
      <div style="background:#fff;padding:28px;border:1px solid #e0e9f5;border-top:none;border-radius:0 0 8px 8px">
        <p style="margin:0 0 16px">Hej {customer_name},</p>
        <p style="margin:0 0 16px">Bifogat finns er rapport för <strong>{month} {year}</strong> från TERAFALK.</p>
        <p style="margin:0 0 16px">Rapporten omfattar: <strong>{included_integrations}</strong></p>
        <p style="margin:0 0 24px">Har ni frågor är ni välkomna att kontakta oss.</p>
        <p style="margin:0;font-size:12px;color:#666">TERAFALK AB · admin@terafalk.com<br>Detta är ett automatiskt utskick — svara inte på detta e-postmeddelande.</p>
      </div>
    </div>
    """
