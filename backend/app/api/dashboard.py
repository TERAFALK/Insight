"""
Dashboard summary endpoint — aggregerar cachad integration-data för alla kunder.
Hämtar data parallellt och fyller på cache om den saknas.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_admin
from app.core.integration_cache import get_cached, set_cached
from app.db.database import get_db
from app.db.models import Customer, IntegrationCredential, User
from app.integrations.registry import get_client

router = APIRouter()
logger = logging.getLogger(__name__)


async def _ensure_cached(customer_id: str, integration_type: str, cred: IntegrationCredential) -> dict | None:
    entry = get_cached(customer_id, integration_type)
    if entry is not None:
        return entry.data
    try:
        client = get_client(integration_type)
        data = await client.fetch_report_data(cred)
        set_cached(customer_id, integration_type, data)
        return data
    except Exception as e:
        logger.warning("Dashboard: kunde inte hämta %s för %s: %s", integration_type, customer_id, e)
        return None


@router.get("/summary")
async def dashboard_summary(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """
    Returnerar aggregerad data för alla kunder med verifierade integrationer.
    Använder cache (stale-while-revalidate) och hämtar live vid cache-miss.
    """
    rows = await db.scalars(
        select(Customer)
        .where(Customer.is_active == True)
        .options(selectinload(Customer.credentials))
        .order_by(Customer.name)
    )
    customers = rows.all()

    # Hämta all integration-data parallellt
    tasks = []
    task_meta = []
    for c in customers:
        for cred in c.credentials:
            if cred.is_verified:
                tasks.append(_ensure_cached(c.id, cred.integration_type, cred))
                task_meta.append((c.id, cred.integration_type))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Bygg upp data per kund
    data_by_customer: dict[str, dict] = {}
    for (cid, itype), result in zip(task_meta, results):
        if isinstance(result, Exception) or result is None:
            continue
        if cid not in data_by_customer:
            data_by_customer[cid] = {}
        data_by_customer[cid][itype] = result

    # Aggregera UniFi-statistik
    unifi_total_devices = 0
    unifi_offline_devices = 0
    unifi_needs_update = 0
    unifi_wan_down = 0
    unifi_wan_total = 0

    # Aggregera Microsoft-statistik
    ms_total_users = 0
    ms_mfa_registered = 0
    ms_mfa_total = 0
    ms_inactive_licensed = 0
    ms_secure_score_sum = 0
    ms_secure_score_max_sum = 0
    ms_customers_with_score = 0

    customer_summaries = []
    for c in customers:
        cdata = data_by_customer.get(c.id, {})
        unifi = cdata.get("unifi")
        ms = cdata.get("microsoft")

        summary = {
            "id": c.id,
            "name": c.name,
            "city": c.city,
            "contact_email": c.contact_email,
            "integrations_verified": [cr.integration_type for cr in c.credentials if cr.is_verified],
        }

        if unifi:
            devices = unifi.get("devices") or []
            total = len(devices) or unifi.get("total_devices", 0)
            offline = sum(1 for d in devices if not d.get("is_online", True))
            needs_upd = sum(1 for d in devices if d.get("needs_update"))
            wans = [w for h in (unifi.get("hosts") or []) for w in (h.get("wans") or [])]
            wan_down = sum(1 for w in wans if (w.get("uptime_percentage") or 0) < 99.9)

            summary["unifi"] = {
                "total_devices": total,
                "offline_devices": offline,
                "needs_update": needs_upd,
                "wan_count": len(wans),
                "wan_down": wan_down,
            }
            unifi_total_devices += total
            unifi_offline_devices += offline
            unifi_needs_update += needs_upd
            unifi_wan_down += wan_down
            unifi_wan_total += len(wans)

        if ms:
            total_u = ms.get("total_users", 0)
            mfa_reg = ms.get("mfa_registered", 0)
            mfa_tot = ms.get("mfa_total", 0)
            inactive = len(ms.get("inactive_licensed_users") or [])
            score = ms.get("secure_score")
            score_max = ms.get("secure_score_max")

            summary["microsoft"] = {
                "total_users": total_u,
                "mfa_registered": mfa_reg,
                "mfa_total": mfa_tot,
                "inactive_licensed": inactive,
                "secure_score": score,
                "secure_score_max": score_max,
            }
            ms_total_users += total_u
            ms_mfa_registered += mfa_reg
            ms_mfa_total += mfa_tot
            ms_inactive_licensed += inactive
            if score is not None and score_max:
                ms_secure_score_sum += score
                ms_secure_score_max_sum += score_max
                ms_customers_with_score += 1

        customer_summaries.append(summary)

    ms_score_pct = None
    if ms_customers_with_score:
        ms_score_pct = round(ms_secure_score_sum / ms_secure_score_max_sum * 100, 1)

    return {
        "customers": customer_summaries,
        "unifi": {
            "total_devices": unifi_total_devices,
            "offline_devices": unifi_offline_devices,
            "needs_update": unifi_needs_update,
            "wan_down": unifi_wan_down,
            "wan_total": unifi_wan_total,
        },
        "microsoft": {
            "total_users": ms_total_users,
            "mfa_registered": ms_mfa_registered,
            "mfa_total": ms_mfa_total,
            "inactive_licensed": ms_inactive_licensed,
            "secure_score_pct": ms_score_pct,
        },
    }
