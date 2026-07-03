"""E-postnotifieringar för ärendehanteringen — bygger på den centrala mailmotorn."""

import logging

from app.core import app_settings
from app.graph.mailer import (
    heading, info_card, paragraph, quote_block, render_email, send_mail, ticket_button,
)

logger = logging.getLogger(__name__)

PRIORITY_LABELS = {"critical": "Kritisk", "high": "Hög", "medium": "Medium", "low": "Låg"}

TYPE_LABELS = {
    "incident": "Incident",
    "service_request": "Serviceförfrågan",
    "change": "Ändringsärende",
    "problem": "Problem",
    "information": "Informationsförfrågan",
}

STATUS_LABELS = {
    "new": "Ny", "open": "Öppen", "in_progress": "Pågår",
    "pending_customer": "Inväntar kund", "resolved": "Löst",
    "closed": "Stängd", "cancelled": "Avbruten",
}

# Ärendemail uppmuntrar svar (till skillnad från rapportmail).
_FOOTER = "TERAFALK AB · Svara på detta mejl med ärendenumret i ämnesraden för att uppdatera ärendet."


async def _get_setting(event_type: str):
    from app.db.database import AsyncSessionLocal
    from app.db.models import NotificationSetting
    async with AsyncSessionLocal() as db:
        return await db.get(NotificationSetting, event_type)


def _add(recipients: list[tuple[str, str]], email: str | None, name: str | None) -> None:
    if email and email not in {r[0] for r in recipients}:
        recipients.append((email, name or email))


def _internal_recipient(cfg) -> tuple[str, str]:
    email = (cfg.internal_email if cfg and cfg.internal_email else None) or \
        app_settings.get("support_inbox") or "support@terafalk.com"
    return email, "TERAFALK Support"


def _customer_recipients(ticket) -> list[tuple[str, str]]:
    """Alla kundsidans mottagare: länkade kontakter + ärendets skapare + ev. mejlavsändare."""
    out: list[tuple[str, str]] = []
    for tc in (ticket.contacts or []):
        c = tc.contact
        if c and c.email and c.is_active:
            _add(out, c.email, c.name)
    cb = getattr(ticket, "created_by", None)
    if cb and getattr(cb, "role", None) == "customer" and cb.email:
        _add(out, cb.email, cb.full_name or cb.email)
    if getattr(ticket, "source_email", None):
        _add(out, ticket.source_email, ticket.source_email)
    return out


async def _send_to_all(
    recipients: list[tuple[str, str]], subject: str, body_html: str,
    attachments: list[dict] | None = None,
) -> None:
    for email, name in recipients:
        try:
            await send_mail(email, name, subject, body_html, attachments=attachments or None)
        except Exception as e:
            logger.warning("Kunde inte skicka ärendemejl till %s: %s", email, e)


# ── Notiser ─────────────────────────────────────────────────────────────────────

async def send_ticket_created(
    ticket_number: str,
    title: str,
    to_email: str,
    to_name: str,
    ticket_id: str | None = None,
) -> None:
    cfg = await _get_setting("ticket_created")
    if cfg and (not cfg.enabled or not cfg.notify_customer):
        return
    content = (
        heading("Ditt ärende har registrerats")
        + paragraph("Tack för din kontakt. Vi har tagit emot ditt ärende och återkommer så snart som möjligt.")
        + info_card([("Ärendenummer", ticket_number), ("Ärende", title)])
        + (ticket_button(ticket_id) if ticket_id else "")
        + paragraph("Du kan följa och svara på ärendet i Insight-portalen, eller via e-post med ärendenumret i ämnesraden.")
    )
    body = render_email(content, footer_note=_FOOTER, preheader=f"Ärende {ticket_number} registrerat")
    await _send_to_all([(to_email, to_name)], f"[{ticket_number}] Ärende registrerat: {title}", body)


async def send_ticket_reply(ticket, replier, message_body: str, is_internal: bool, attachments: list[dict] | None = None) -> None:
    """Notifiera rätt mottagare när ett (publikt) svar postas."""
    if is_internal:
        return
    t = ticket

    if replier.role == "admin":
        cfg = await _get_setting("ticket_reply_staff")
        if cfg and not cfg.enabled:
            return
        recipients: list[tuple[str, str]] = []
        if not cfg or cfg.notify_customer:
            recipients.extend(_customer_recipients(t))
        if cfg and cfg.notify_internal:
            _add(recipients, *_internal_recipient(cfg))
        if not recipients:
            return
        content = (
            heading("Nytt svar på ditt ärende")
            + info_card([("Ärende", f"{t.ticket_number} — {t.title}")])
            + quote_block(message_body)
            + ticket_button(t.id)
            + paragraph("Logga in i Insight-portalen för att svara, eller svara på detta mejl.")
        )
        body = render_email(content, footer_note=_FOOTER, preheader=f"Nytt svar på {t.ticket_number}")
        await _send_to_all(recipients, f"[{t.ticket_number}] Nytt svar: {t.title}", body, attachments)
    else:
        cfg = await _get_setting("ticket_reply_customer")
        if cfg and not cfg.enabled:
            return
        recipients = []
        if (not cfg or cfg.notify_assigned) and t.assigned_to and t.assigned_to.email:
            _add(recipients, t.assigned_to.email, t.assigned_to.full_name or t.assigned_to.email)
        if cfg and cfg.notify_internal:
            _add(recipients, *_internal_recipient(cfg))
        if not recipients:
            _add(recipients, *_internal_recipient(cfg))
        content = (
            heading("Kunden har svarat på ärende")
            + info_card([
                ("Ärende", f"{t.ticket_number} — {t.title}"),
                ("Kund", t.customer.name if t.customer else "—"),
                ("Prioritet", PRIORITY_LABELS.get(t.priority, t.priority)),
            ])
            + quote_block(message_body)
            + ticket_button(t.id)
        )
        body = render_email(content, footer_note=_FOOTER, preheader=f"Kundsvar på {t.ticket_number}")
        await _send_to_all(recipients, f"[{t.ticket_number}] Kundsvar: {t.title}", body, attachments)


async def send_ticket_assigned(ticket, assigned_user) -> None:
    cfg = await _get_setting("ticket_assigned")
    if not cfg or not cfg.enabled:
        return
    t = ticket
    recipients: list[tuple[str, str]] = []
    if cfg.notify_assigned and assigned_user and assigned_user.email:
        _add(recipients, assigned_user.email, assigned_user.full_name or assigned_user.email)
    if cfg.notify_internal:
        _add(recipients, *_internal_recipient(cfg))
    if not recipients:
        return
    content = (
        heading("Ärende tilldelat dig")
        + info_card([
            ("Ärende", f"{t.ticket_number} — {t.title}"),
            ("Kund", t.customer.name if t.customer else "—"),
            ("Prioritet", PRIORITY_LABELS.get(t.priority, t.priority)),
        ])
        + ticket_button(t.id)
    )
    body = render_email(content, footer_note=_FOOTER, preheader=f"Tilldelat: {t.ticket_number}")
    await _send_to_all(recipients, f"[{t.ticket_number}] Tilldelat: {t.title}", body)


async def send_ticket_status_changed(ticket, old_status: str, new_status: str) -> None:
    cfg = await _get_setting("ticket_status_changed")
    if not cfg or not cfg.enabled:
        return
    t = ticket
    recipients: list[tuple[str, str]] = []
    if cfg.notify_customer:
        recipients.extend(_customer_recipients(t))
    if cfg.notify_assigned and t.assigned_to and t.assigned_to.email:
        _add(recipients, t.assigned_to.email, t.assigned_to.full_name or t.assigned_to.email)
    if cfg.notify_internal:
        _add(recipients, *_internal_recipient(cfg))
    if not recipients:
        return
    content = (
        heading("Status uppdaterad på ditt ärende")
        + info_card([
            ("Ärende", f"{t.ticket_number} — {t.title}"),
            ("Status", f"{STATUS_LABELS.get(old_status, old_status)} → {STATUS_LABELS.get(new_status, new_status)}"),
        ])
        + ticket_button(t.id)
    )
    body = render_email(content, footer_note=_FOOTER, preheader=f"Status: {STATUS_LABELS.get(new_status, new_status)}")
    await _send_to_all(recipients, f"[{t.ticket_number}] Status: {STATUS_LABELS.get(new_status, new_status)}", body)


async def send_ticket_resolved(ticket) -> None:
    """Notis till kunden när ett ärende markeras som löst."""
    cfg = await _get_setting("ticket_resolved")
    if not cfg or not cfg.enabled:
        return
    t = ticket
    recipients: list[tuple[str, str]] = []
    if cfg.notify_customer:
        recipients.extend(_customer_recipients(t))
    if cfg.notify_internal:
        _add(recipients, *_internal_recipient(cfg))
    if not recipients:
        return
    resolution = (t.resolution or "").strip()
    content = (
        heading("Ditt ärende är löst")
        + paragraph("Vi har markerat ditt ärende som löst. Hör av dig om du behöver ytterligare hjälp — "
                    "svara på detta mejl så öppnas ärendet igen.")
        + info_card([("Ärende", f"{t.ticket_number} — {t.title}")])
        + (quote_block(resolution) if resolution else "")
        + ticket_button(t.id)
    )
    body = render_email(content, footer_note=_FOOTER, preheader=f"Ärende {t.ticket_number} löst")
    await _send_to_all(recipients, f"[{t.ticket_number}] Ärende löst: {t.title}", body)


async def send_csat_survey(ticket, recipients: list[tuple[str, str]]) -> None:
    """Fördröjd nöjdhetsenkät med inloggningsfria stjärnlänkar till den publika sidan."""
    from app.graph.mailer import portal_url
    base = portal_url()
    if not base or not ticket.csat_token or not recipients:
        return
    t = ticket

    def star(n: int) -> str:
        url = f"{base}/?csat={t.id}&token={t.csat_token}&score={n}"
        return f'<a href="{url}" style="text-decoration:none;font-size:30px;color:#f59e0b;margin:0 3px">★</a>'

    stars = "".join(star(n) for n in range(1, 6))
    content = (
        heading("Hur nöjd är du med hanteringen?")
        + paragraph(f"Ditt ärende <strong>{t.ticket_number}</strong> — {html_escape(t.title)} har lösts. "
                    "Vi uppskattar din återkoppling.")
        + f'<div style="text-align:center;margin:6px 0 14px">{stars}</div>'
        + paragraph("Klicka på antal stjärnor ovan för att lämna ditt betyg — du kan även skriva en kommentar. "
                    "Ingen inloggning krävs.")
    )
    body = render_email(content, footer_note=_FOOTER, preheader=f"Betygsätt ärende {t.ticket_number}")
    await _send_to_all(recipients, f"[{t.ticket_number}] Hur nöjd är du med hanteringen?", body)


async def send_ticket_mention(ticket, mentioned_user, note_body: str, author) -> None:
    """Notifiera en kollega som @mentionats i en intern notering."""
    cfg = await _get_setting("ticket_mention")
    if cfg and not cfg.enabled:
        return
    if not (mentioned_user and mentioned_user.email):
        return
    t = ticket
    author_name = (author.full_name or author.email) if author else "En kollega"
    content = (
        heading("Du har nämnts i ett ärende")
        + paragraph(f"{html_escape(author_name)} nämnde dig i en intern notering.")
        + info_card([("Ärende", f"{t.ticket_number} — {t.title}")])
        + quote_block(note_body)
        + ticket_button(t.id)
    )
    body = render_email(content, footer_note=_FOOTER, preheader=f"Nämnd i {t.ticket_number}")
    await _send_to_all(
        [(mentioned_user.email, mentioned_user.full_name or mentioned_user.email)],
        f"[{t.ticket_number}] Du nämndes: {t.title}", body,
    )


def html_escape(s: str) -> str:
    import html as _html
    return _html.escape(s or "")


async def send_sla_breach_warning(ticket, kind: str = "resolution") -> None:
    cfg = await _get_setting("ticket_sla_warning")
    if cfg and not cfg.enabled:
        return
    t = ticket
    recipients: list[tuple[str, str]] = []
    if t.assigned_to and t.assigned_to.email:
        _add(recipients, t.assigned_to.email, t.assigned_to.full_name or t.assigned_to.email)
    else:
        _add(recipients, *_internal_recipient(cfg))

    is_response = kind == "response"
    label = "Svars-SLA (första svar)" if is_response else "Lösnings-SLA"
    due = t.first_response_due_at if is_response else t.sla_due_at
    content = (
        heading("⚠️ SLA-varning")
        + paragraph(f"Nedanstående ärende har brutit {label.lower()}.")
        + info_card([
            ("Ärende", f"{t.ticket_number} — {t.title}"),
            ("Kund", t.customer.name if t.customer else "—"),
            ("Prioritet", PRIORITY_LABELS.get(t.priority, t.priority)),
            (label, due.strftime("%Y-%m-%d %H:%M") if due else "—"),
        ])
        + ticket_button(t.id)
    )
    body = render_email(content, footer_note=_FOOTER, preheader=f"SLA-varning {t.ticket_number}")
    await _send_to_all(recipients, f"[SLA-VARNING] {t.ticket_number}: {t.title}", body)
