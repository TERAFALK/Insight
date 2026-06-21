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

        month_names = {
            "01": "januari", "02": "februari", "03": "mars", "04": "april",
            "05": "maj", "06": "juni", "07": "juli", "08": "augusti",
            "09": "september", "10": "oktober", "11": "november", "12": "december",
        }
        month_display = {
            "01": "Januari", "02": "Februari", "03": "Mars", "04": "April",
            "05": "Maj", "06": "Juni", "07": "Juli", "08": "Augusti",
            "09": "September", "10": "Oktober", "11": "November", "12": "December",
        }
        year, month_num = period.split("-")
        month_sv = month_names.get(month_num, month_num)
        month_cap = month_display.get(month_num, month_num)
        included = ", ".join(INTEGRATIONS[k].display_name for k in sections.keys())

        try:
            await send_report_email(
                to_email=customer.contact_email,
                to_name=customer.contact_name or customer.name,
                subject=f"IT-Rapport {month_cap} {year} — {customer.name}",
                body_html=_email_body(customer.name, month_sv, year, included),
                pdf_path=pdf_path,
                pdf_filename=f"{customer.name} - {period}.pdf",
            )
            report.send_status = "sent"
            report.sent_at = now_stockholm()
        except Exception as e:
            logger.error("Graph-utskick misslyckades för %s: %s", customer.name, e)
            report.send_status = "error"
            report.error_message = str(e)

        await db.commit()
        logger.info("Rapport klar för %s (%s) — sektioner: %s", customer.name, period, list(sections.keys()))


_LOGO_B64 = "PHN2ZyBpZD0iTGF5ZXJfMSIgZGF0YS1uYW1lPSJMYXllciAxIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2OTUuMzkgODQuMjQiPjxwYXRoIGQ9Ik0yMzYuMTgsNDU1LjU3djE1SDIwMS43NHY2OWgtMTV2LTY5SDE1Mi4zdi0xNVoiIHRyYW5zZm9ybT0idHJhbnNsYXRlKC0xNTIuMyAtNDU1LjQ1KSIvPjxwYXRoIGQ9Ik0yNjMuMyw0NzguMTN2Ny44aDU0djE1aC01NHYxNS44NGE3Ljc0LDcuNzQsMCwwLDAsNy42OCw3LjY4SDMzMi4zdjE1SDI3MWEyMi42MywyMi42MywwLDAsMS0yMi41Ni0yMi42N1Y0NzguMTNBMjIuNjQsMjIuNjQsMCwwLDEsMjcxLDQ1NS40NUgzMzIuM3YxNUgyNzFBNy43Myw3LjczLDAsMCwwLDI2My4zLDQ3OC4xM1oiIHRyYW5zZm9ybT0idHJhbnNsYXRlKC0xNTIuMyAtNDU1LjQ1KSIvPjxwYXRoIGQ9Ik00MTIuMSw1MjQuNjlsNy42OCwxNUg0MDNsLTcuNjgtMTUtOC0xNS43Mi0uMzYtLjcyYTE0Ljg3LDE0Ljg3LDAsMCwwLTEyLjcyLTcuMmgtMTV2MzguNjNoLTE1di04NGg1M2EyMi41MywyMi41MywwLDAsMSwyMi41NiwyMi41NiwyMi43NSwyMi43NSwwLDAsMS0xMy4yLDIwLjY0LDIwLDIwLDAsMCwxLTYuNDgsMS44Wm0tMTQuODgtMzguNjRhNyw3LDAsMCwwLDMuMTItLjcyLDcuNjIsNy42MiwwLDAsMCw0LjU2LTcsNy45Miw3LjkyLDAsMCwwLTIuMjgtNS41Miw3LjU2LDcuNTYsMCwwLDAtNS40LTIuMTZoLTM4djE1LjQ4WiIgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoLTE1Mi4zIC00NTUuNDUpIi8+PHBhdGggZD0iTTUxMi43OCw1MzkuNDRINDk2bC03LjY4LTE1LTE4LjM2LTM2LTE4LjM2LDM2LTcuNjgsMTVINDI3LjFsNy42OC0xNSwzNS4xNi02OSwzNS4xNiw2OVoiIHRyYW5zZm9ybT0idHJhbnNsYXRlKC0xNTIuMyAtNDU1LjQ1KSIvPjxwYXRoIGQ9Ik02MDQuMSw0NTUuNDV2MTVINTQyLjc4YTcuNzMsNy43MywwLDAsMC03LjY4LDcuNjh2Ny44aDU0djE1SDUzNXYzOC41MUg1MjAuMVY0NzguMTNhMjIuNjQsMjIuNjQsMCwwLDEsMjIuNTYtMjIuNjhaIiB0cmFuc2Zvcm09InRyYW5zbGF0ZSgtMTUyLjMgLTQ1NS40NSkiLz48cGF0aCBkPSJNNjc4LjM4LDUzOS40NGgtMTYuOGwtNy42OC0xNS0xOC4zNi0zNi0xOC4zNiwzNi03LjY4LDE1SDU5Mi43bDcuNjgtMTUsMzUuMTYtNjksMzUuMTYsNjlaIiB0cmFuc2Zvcm09InRyYW5zbGF0ZSgtMTUyLjMgLTQ1NS40NSkiLz48cGF0aCBkPSJNNzUxLjcsNTI0LjU3djE1SDcwOS4xYTI2LjA5LDI2LjA5LDAsMCwxLTExLjc2LTIuNzYsMjYuNTksMjYuNTksMCwwLDEtMTIuMTItMTIuMjMsMjYuMTIsMjYuMTIsMCwwLDEtMi43Ni0xMS43NlY0NTUuNTdoMTV2NTguNjhhMTIsMTIsMCwwLDAsMTAuMiwxMC4yWiIgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoLTE1Mi4zIC00NTUuNDUpIi8+PHBhdGggZD0iTTgxMSw0ODkuMTdsMzYuMzUsNTAuMjdIODI4Ljg1bC0yOS00MC4wNy0yMS4yNCwxOS4zMnYyMC43NWgtMTV2LTg0aDE1djQzbDEyLjM2LTExLjI4LDExLjE2LTEwLjIsMjMuMzktMjEuNDhoMjIuMloiIHRyYW5zZm9ybT0idHJhbnNsYXRlKC0xNTIuMyAtNDU1LjQ1KSIvPjwvc3ZnPg=="


def _email_body(customer_name: str, month: str, year: str, included_integrations: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#141414">
      <div style="background:#fff;padding:22px 28px;border:1px solid #e0e9f5;border-radius:8px 8px 0 0">
        <img src="data:image/svg+xml;base64,{_LOGO_B64}" width="130" height="16" alt="TERAFALK" style="display:block">
      </div>
      <div style="background:#fff;padding:28px;border:1px solid #e0e9f5;border-top:none;border-radius:0 0 8px 8px">
        <p style="margin:0 0 16px">Hej {customer_name},</p>
        <p style="margin:0 0 16px">Bifogat finns er IT-rapport för <strong>{month} {year}</strong> från TERAFALK.</p>
        <p style="margin:0 0 16px">Rapporten omfattar: <strong>{included_integrations}</strong></p>
        <p style="margin:0 0 24px">Har ni frågor är ni välkomna att kontakta oss på support@terafalk.com.</p>
        <p style="margin:0;font-size:12px;color:#666">TERAFALK AB<br>Detta är ett automatiskt utskick — svara inte på detta e-postmeddelande.</p>
      </div>
    </div>
    """
