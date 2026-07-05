"""E-postnotifieringar för ordrar och projekt — bygger på den centrala mailmotorn."""

import logging

from app.core import app_settings
from app.graph.mailer import heading, info_card, render_email, send_mail

logger = logging.getLogger(__name__)


async def _get_setting(event_type: str):
    from app.db.database import AsyncSessionLocal
    from app.db.models import NotificationSetting
    async with AsyncSessionLocal() as db:
        return await db.get(NotificationSetting, event_type)


async def _notify(event_type: str, order, subject: str, content: str) -> None:
    """Skicka notis baserat på konfiguration för händelsetypen."""
    cfg = await _get_setting(event_type)
    if not cfg or not cfg.enabled:
        return

    recipients: list[tuple[str, str]] = []
    seen = set()

    def add(email, name):
        if email and email not in seen:
            seen.add(email)
            recipients.append((email, name or email))

    if cfg.notify_customer:
        for oc in (order.contacts or []):
            c = oc.contact
            if c and c.email and c.is_active:
                add(c.email, c.name)
    if cfg.notify_assigned and order.assigned_to and order.assigned_to.email:
        add(order.assigned_to.email, order.assigned_to.full_name or order.assigned_to.email)
    if cfg.notify_internal:
        internal = cfg.internal_email or app_settings.get("support_inbox") or "support@terafalk.com"
        add(internal, "TERAFALK Support")

    if not recipients:
        return

    body = render_email(content, preheader=subject)
    for email, name in recipients:
        try:
            await send_mail(email, name, subject, body)
        except Exception as e:
            logger.warning("Kunde inte skicka ordermejl till %s: %s", email, e)


def _type_label(order) -> str:
    return "Projekt" if order.type == "project" else "Order"


async def send_order_created(order) -> None:
    label = _type_label(order)
    content = (
        heading(f"Ny {label.lower()} skapad")
        + info_card([
            ("Kund", order.customer.name if order.customer else "—"),
            (label, order.title),
        ])
    )
    await _notify("order_created", order, f"{label}: {order.title}", content)


async def send_order_status_changed(order, old_status: str, new_status: str) -> None:
    label = _type_label(order)
    status_map = {"active": "Aktiv", "completed": "Avslutad", "cancelled": "Makulerad"}
    content = (
        heading(f"{label}status ändrad")
        + info_card([
            (label, order.title),
            ("Status", f"{status_map.get(old_status, old_status)} → {status_map.get(new_status, new_status)}"),
        ])
    )
    await _notify("order_status_changed", order, f"{label} uppdaterad: {order.title}", content)


async def send_order_phase_changed(order, old_phase: str, new_phase: str) -> None:
    label = _type_label(order)
    content = (
        heading(f"{label}fas uppdaterad")
        + info_card([
            (label, order.title),
            ("Fas", f"{old_phase or '—'} → {new_phase or '—'}"),
        ])
    )
    await _notify("order_phase_changed", order, f"{label} fas ändrad: {order.title}", content)
