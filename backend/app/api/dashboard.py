"""
Dashboard summary endpoint — aggregerar cachad integration-data för alla kunder.
Hämtar data parallellt och fyller på cache om den saknas.
"""

import asyncio
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import current_user, require_admin
from app.core.integration_cache import get_cached, set_cached
from app.core.time_utils import now_stockholm
from app.db.database import get_db
from app.db.models import (
    Customer,
    CustomerServiceArticle,
    IntegrationCredential,
    Report,
    ServiceArticle,
    Ticket,
    User,
)
from app.api.services import _monthly_value
from app.integrations.registry import get_client

router = APIRouter()
logger = logging.getLogger(__name__)

# Ärendestatusar som räknas som "öppna" (kräver fortfarande arbete)
OPEN_TICKET_STATUSES = ("new", "open", "in_progress", "pending_customer")


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
            "contact_name": c.contact_name,
            "integrations_verified": [cr.integration_type for cr in c.credentials if cr.is_verified],
        }

        if unifi:
            devices = unifi.get("devices") or []
            total = len(devices) or unifi.get("total_devices", 0)
            offline = sum(1 for d in devices if not d.get("is_online", True))
            needs_upd = sum(1 for d in devices if d.get("needs_update"))
            wans = [w for h in (unifi.get("hosts") or []) for w in (h.get("wans") or [])]
            wan_down = sum(1 for w in wans if not w.get("is_up", True))

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


def _open_ticket_dict(t: Ticket) -> dict:
    return {
        "id": t.id,
        "ticket_number": t.ticket_number,
        "title": t.title,
        "status": t.status,
        "priority": t.priority,
        "customer_name": t.customer.name if t.customer else "—",
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "sla_breached": bool(t.sla_breached or t.response_sla_breached),
    }


@router.get("/admin")
async def admin_dashboard(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Lättviktiga aggregat för admin-översikten: ärenden, trend, tjänster."""
    # Ärenden per status
    status_rows = (await db.execute(
        select(Ticket.status, func.count()).group_by(Ticket.status)
    )).all()
    status_counts = {status: count for status, count in status_rows}
    open_total = sum(status_counts.get(s, 0) for s in OPEN_TICKET_STATUSES)

    # SLA-brott bland öppna ärenden
    sla_breached = await db.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.status.in_(OPEN_TICKET_STATUSES),
            (Ticket.sla_breached == True) | (Ticket.response_sla_breached == True),
        )
    ) or 0

    # Trend: skapade + lösta per dag, senaste 14 dagarna
    today = now_stockholm().date()
    start = today - timedelta(days=13)
    created_rows = (await db.execute(
        select(func.date(Ticket.created_at), func.count())
        .where(Ticket.created_at >= start)
        .group_by(func.date(Ticket.created_at))
    )).all()
    resolved_rows = (await db.execute(
        select(func.date(Ticket.resolved_at), func.count())
        .where(Ticket.resolved_at.isnot(None), Ticket.resolved_at >= start)
        .group_by(func.date(Ticket.resolved_at))
    )).all()
    created_by_day = {str(d): c for d, c in created_rows}
    resolved_by_day = {str(d): c for d, c in resolved_rows}
    trend = []
    for i in range(14):
        d = start + timedelta(days=i)
        key = d.isoformat()
        trend.append({
            "date": key,
            "created": created_by_day.get(key, 0),
            "resolved": resolved_by_day.get(key, 0),
        })

    # Senaste öppna ärenden (topp 8)
    rows = await db.scalars(
        select(Ticket)
        .where(Ticket.status.in_(OPEN_TICKET_STATUSES))
        .options(selectinload(Ticket.customer))
        .order_by(Ticket.created_at.desc())
        .limit(8)
    )
    recent_open = [_open_ticket_dict(t) for t in rows.all()]

    # "Mina ärenden" — öppna ärenden tilldelade den inloggade admin, SLA-sorterade
    my_rows = await db.scalars(
        select(Ticket)
        .where(Ticket.status.in_(OPEN_TICKET_STATUSES), Ticket.assigned_to_user_id == admin.id)
        .options(selectinload(Ticket.customer))
        .order_by(Ticket.sla_due_at.asc().nulls_last())
        .limit(8)
    )
    my_open = [_open_ticket_dict(t) for t in my_rows.all()]
    my_open_total = await db.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.status.in_(OPEN_TICKET_STATUSES), Ticket.assigned_to_user_id == admin.id
        )
    ) or 0

    # Artikelbaserad MRR (informativt): normaliserat månadsvärde per aktiv avtalsrad.
    # Aggregeras i Python för att matcha _monthly_value (MSRP ÷ cykel × antal).
    assign_rows = await db.scalars(
        select(CustomerServiceArticle)
        .where(CustomerServiceArticle.status == "active")
        .options(
            selectinload(CustomerServiceArticle.article).selectinload(ServiceArticle.service),
            selectinload(CustomerServiceArticle.customer),
        )
    )
    assignments = assign_rows.all()

    per_service: dict[str, dict] = {}
    per_customer: dict[str, dict] = {}
    mrr_total = 0
    for a in assignments:
        art = a.article
        svc = art.service if art else None
        if not svc:
            continue
        val = _monthly_value(art, a.quantity)
        mrr_total += val
        ps = per_service.setdefault(svc.id, {
            "name": svc.name, "icon": svc.icon, "color": svc.color, "customers": set(), "mrr": 0,
        })
        ps["customers"].add(a.customer_id)
        ps["mrr"] += val
        pc = per_customer.setdefault(a.customer_id, {
            "name": a.customer.name if a.customer else "—", "mrr": 0,
        })
        pc["mrr"] += val

    service_adoption = sorted(
        [{"name": v["name"], "icon": v["icon"], "color": v["color"],
          "count": len(v["customers"]), "mrr": v["mrr"]} for v in per_service.values()],
        key=lambda x: (-x["mrr"], x["name"]),
    )
    active_services_total = len(assignments)
    top_customers = sorted(
        [{"id": cid, "name": v["name"], "mrr": v["mrr"]} for cid, v in per_customer.items()],
        key=lambda x: -x["mrr"],
    )[:6]

    return {
        "tickets": {
            "open_total": open_total,
            "sla_breached": int(sla_breached),
            "by_status": status_counts,
            "trend": trend,
            "recent_open": recent_open,
            "my_open_total": int(my_open_total),
            "my_open": my_open,
        },
        "services": {
            "active_total": int(active_services_total),
            "mrr_total": int(mrr_total),
            "adoption": service_adoption,
            "top_customers": top_customers,
        },
    }


@router.get("/customer")
async def customer_dashboard(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Översiktsdata för en inloggad kundanvändare (scopad på egen kund)."""
    if user.role != "customer" or not user.customer_id:
        raise HTTPException(403, "Endast för kundanvändare")
    cid = user.customer_id

    customer = await db.scalar(select(Customer).where(Customer.id == cid))
    if not customer:
        raise HTTPException(404, "Kund hittades inte")

    # Öppna ärenden
    open_rows = await db.scalars(
        select(Ticket)
        .where(Ticket.customer_id == cid, Ticket.status.in_(OPEN_TICKET_STATUSES))
        .options(selectinload(Ticket.customer))
        .order_by(Ticket.created_at.desc())
        .limit(6)
    )
    open_list = open_rows.all()
    open_total = await db.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.customer_id == cid, Ticket.status.in_(OPEN_TICKET_STATUSES)
        )
    ) or 0

    # Tjänster grupperade från kundens artiklar. En tjänst är aktiv om den har
    # minst en aktiv artikel.
    art_rows = await db.scalars(
        select(CustomerServiceArticle)
        .where(CustomerServiceArticle.customer_id == cid)
        .options(selectinload(CustomerServiceArticle.article).selectinload(ServiceArticle.service))
    )
    svc_map: dict[str, dict] = {}
    for csa in art_rows.all():
        art = csa.article
        svc = art.service if art else None
        if not svc:
            continue
        entry = svc_map.setdefault(svc.id, {
            "id": svc.id,
            "name": svc.name,
            "icon": svc.icon,
            "color": svc.color,
            "description": svc.description,
            "status": "ended",
            "articles": [],
        })
        entry["articles"].append({
            "name": art.name,
            "quantity": csa.quantity,
            "status": csa.status,
        })
        if csa.status == "active":
            entry["status"] = "active"
        elif csa.status == "paused" and entry["status"] != "active":
            entry["status"] = "paused"
    services = sorted(svc_map.values(), key=lambda s: (s["status"] != "active", s["name"]))

    # Drift-hälsa från cache (ingen live-hämtning här)
    health = {}
    unifi = get_cached(cid, "unifi")
    if unifi is not None:
        devices = unifi.data.get("devices") or []
        offline = sum(1 for d in devices if not d.get("is_online", True))
        wans = [w for h in (unifi.data.get("hosts") or []) for w in (h.get("wans") or [])]
        wan_down = sum(1 for w in wans if not w.get("is_up", True))
        health["unifi"] = {
            "total_devices": len(devices) or unifi.data.get("total_devices", 0),
            "offline_devices": offline,
            "wan_total": len(wans),
            "wan_down": wan_down,
        }
    ms = get_cached(cid, "microsoft")
    if ms is not None:
        mfa_reg = ms.data.get("mfa_registered", 0)
        mfa_tot = ms.data.get("mfa_total", 0)
        health["microsoft"] = {
            "total_users": ms.data.get("total_users", 0),
            "mfa_pct": round(mfa_reg / mfa_tot * 100) if mfa_tot else None,
            "secure_score": ms.data.get("secure_score"),
            "secure_score_max": ms.data.get("secure_score_max"),
        }

    # Senaste rapport
    last_report = await db.scalar(
        select(Report)
        .where(Report.customer_id == cid, Report.send_status == "sent")
        .order_by(Report.created_at.desc())
        .limit(1)
    )
    report = None
    if last_report:
        report = {
            "id": last_report.id,
            "period": last_report.period,
            "sent_at": last_report.sent_at.isoformat() if last_report.sent_at else None,
            "has_pdf": bool(last_report.pdf_path),
        }

    return {
        "customer": {"id": customer.id, "name": customer.name, "city": customer.city},
        "tickets": {
            "open_total": int(open_total),
            "recent_open": [_open_ticket_dict(t) for t in open_list],
        },
        "services": services,
        "health": health,
        "last_report": report,
    }
