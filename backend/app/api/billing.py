"""Fakturaunderlag — summerar debiterbar tid (och MRR) per kund och månad."""

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.db.database import get_db
from app.db.models import (
    Customer,
    Order,
    Ticket,
    TicketTimeEntry,
    TimeEntry,
    User,
)

router = APIRouter()


def _month_bounds(month: str | None) -> tuple[date, date, str]:
    """Returnerar (start, exkl_slut, 'YYYY-MM') för angiven månad (default innevarande)."""
    today = date.today()
    if month:
        try:
            y, m = month.split("-")
            y, m = int(y), int(m)
        except (ValueError, AttributeError):
            y, m = today.year, today.month
    else:
        y, m = today.year, today.month
    start = date(y, m, 1)
    end = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return start, end, f"{y:04d}-{m:02d}"


@router.get("")
async def billing_summary(
    month: str | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Debiterbar tid per kund för en månad. (Avtal/MRR faktureras separat.)"""
    start, end, period = _month_bounds(month)

    # Debiterbar tid från ärenden
    ticket_rows = (await db.execute(
        select(Ticket.customer_id, func.coalesce(func.sum(TicketTimeEntry.billed_minutes), 0))
        .select_from(TicketTimeEntry)
        .join(Ticket, Ticket.id == TicketTimeEntry.ticket_id)
        .where(TicketTimeEntry.worked_at >= start, TicketTimeEntry.worked_at < end)
        .group_by(Ticket.customer_id)
    )).all()

    # Debiterbar tid från ordrar/projekt
    order_rows = (await db.execute(
        select(Order.customer_id, func.coalesce(func.sum(TimeEntry.billed_minutes), 0))
        .select_from(TimeEntry)
        .join(Order, Order.id == TimeEntry.order_id)
        .where(TimeEntry.worked_at >= start, TimeEntry.worked_at < end)
        .group_by(Order.customer_id)
    )).all()

    ticket_min = {cid: int(m or 0) for cid, m in ticket_rows}
    order_min = {cid: int(m or 0) for cid, m in order_rows}

    # Endast kunder med debiterbar tid denna månad
    customer_ids = set(ticket_min) | set(order_min)
    customers = (await db.scalars(
        select(Customer).where(Customer.id.in_(customer_ids)).order_by(Customer.name)
    )).all() if customer_ids else []

    rows = []
    tot_ticket = tot_order = 0
    for c in customers:
        tmin = ticket_min.get(c.id, 0)
        omin = order_min.get(c.id, 0)
        if not (tmin or omin):
            continue
        tot_ticket += tmin
        tot_order += omin
        rows.append({
            "customer_id": c.id,
            "customer_name": c.name,
            "ticket_minutes": tmin,
            "order_minutes": omin,
            "billed_minutes": tmin + omin,
            "billed_hours": round((tmin + omin) / 60, 2),
        })

    return {
        "period": period,
        "rows": rows,
        "totals": {
            "ticket_minutes": tot_ticket,
            "order_minutes": tot_order,
            "billed_minutes": tot_ticket + tot_order,
            "billed_hours": round((tot_ticket + tot_order) / 60, 2),
        },
    }
