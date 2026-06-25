"""E-postnotifieringar för ärendehanteringen via Microsoft Graph."""

import logging

from app.core import app_settings

logger = logging.getLogger(__name__)

PRIORITY_LABELS = {
    "critical": "Kritisk",
    "high": "Hög",
    "medium": "Medium",
    "low": "Låg",
}

TYPE_LABELS = {
    "incident": "Incident",
    "service_request": "Serviceförfrågan",
    "change": "Ändringsärende",
    "problem": "Problem",
    "information": "Informationsförfrågan",
}


async def _send(to_email: str, to_name: str, subject: str, body_html: str) -> None:
    from app.graph.sender import _get_token
    import httpx

    if not app_settings.get("graph_tenant_id"):
        logger.warning("Graph ej konfigurerat — hoppar över ärendemejl till %s", to_email)
        return

    sender = app_settings.get("support_inbox") or "support@terafalk.com"
    token = await _get_token()
    url = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": to_email, "name": to_name}}],
        },
        "saveToSentItems": False,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
    logger.info("Ärendemejl skickat till %s: %s", to_email, subject)


def _base_html(content: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1a1a1a">
      <div style="background:#0047a3;padding:20px 24px;border-radius:8px 8px 0 0">
        <span style="color:#fff;font-size:18px;font-weight:700">TERAFALK Support</span>
      </div>
      <div style="background:#f8f9fa;padding:24px;border-radius:0 0 8px 8px;border:1px solid #e0e0e0;border-top:none">
        {content}
      </div>
      <p style="font-size:11px;color:#888;text-align:center;margin-top:12px">
        TERAFALK AB · support@terafalk.com
      </p>
    </div>
    """


async def send_ticket_created(
    ticket_number: str,
    title: str,
    to_email: str,
    to_name: str,
) -> None:
    content = f"""
    <h2 style="margin:0 0 16px;font-size:16px">Ditt ärende har registrerats</h2>
    <p>Tack för din kontakt. Vi har tagit emot ditt ärende och återkommer så snart som möjligt.</p>
    <div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:16px;margin:16px 0">
      <div style="font-size:13px;color:#555;margin-bottom:4px">Ärendenummer</div>
      <div style="font-size:20px;font-weight:700;color:#0047a3">{ticket_number}</div>
      <div style="font-size:13px;color:#555;margin-top:12px;margin-bottom:4px">Ärende</div>
      <div style="font-weight:600">{title}</div>
    </div>
    <p style="font-size:13px;color:#555">
      Du kan följa ditt ärende och svara direkt i Insight-portalen, eller via e-post med ärendenumret i ämnesraden.
    </p>
    """
    await _send(to_email, to_name, f"[{ticket_number}] Ärende registrerat: {title}", _base_html(content))


async def send_ticket_reply(ticket, replier, message_body: str, is_internal: bool) -> None:
    """Notifiera rätt mottagare när ett svar postas."""
    if is_internal:
        return  # Interna noter skickas inte

    from app.db.models import Ticket
    t: Ticket = ticket

    prio_label = PRIORITY_LABELS.get(t.priority, t.priority)

    if replier.role in ("admin", "technician"):
        # Personal svarar → notifiera kund + ärendekontakter
        recipients = []
        if t.customer and t.customer.contact_email:
            recipients.append((t.customer.contact_email, t.customer.name))
        # Lägg till ärendets kontaktpersoner
        for tc in (t.contacts or []):
            c = tc.contact
            if c and c.email and c.is_active:
                if c.email not in {r[0] for r in recipients}:
                    recipients.append((c.email, c.name))

        if not recipients:
            return

        content = f"""
        <h2 style="margin:0 0 16px;font-size:16px">Nytt svar på ditt ärende</h2>
        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:16px;margin-bottom:16px">
          <div style="font-size:12px;color:#555">Ärende</div>
          <div style="font-weight:700;color:#0047a3">{t.ticket_number} — {t.title}</div>
        </div>
        <div style="background:#fff;border-left:3px solid #0047a3;padding:12px 16px;border-radius:0 6px 6px 0;margin-bottom:16px">
          {message_body}
        </div>
        <p style="font-size:13px;color:#555">Logga in i Insight-portalen för att svara, eller svara på detta e-post.</p>
        """
        subject = f"[{t.ticket_number}] Nytt svar: {t.title}"
        for email, name in recipients:
            await _send(email, name, subject, _base_html(content))
    else:
        # Kund svarar → notifiera tilldelad tekniker eller support@
        if t.assigned_to and t.assigned_to.email:
            target_email = t.assigned_to.email
            target_name  = t.assigned_to.full_name or t.assigned_to.email
        else:
            target_email = app_settings.get("support_inbox") or "support@terafalk.com"
            target_name  = "TERAFALK Support"

        content = f"""
        <h2 style="margin:0 0 16px;font-size:16px">Kunden har svarat på ärende</h2>
        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:16px;margin-bottom:16px">
          <div style="font-size:12px;color:#555">Ärende</div>
          <div style="font-weight:700;color:#0047a3">{t.ticket_number} — {t.title}</div>
          <div style="font-size:12px;color:#555;margin-top:8px">Kund: {t.customer.name if t.customer else '—'} · Prioritet: {prio_label}</div>
        </div>
        <div style="background:#fff;border-left:3px solid #34a853;padding:12px 16px;border-radius:0 6px 6px 0;margin-bottom:16px">
          {message_body}
        </div>
        """
        await _send(target_email, target_name, f"[{t.ticket_number}] Kundsvar: {t.title}", _base_html(content))


async def send_sla_breach_warning(ticket) -> None:
    """Varning när SLA är på väg att brytas."""
    t = ticket
    if t.assigned_to and t.assigned_to.email:
        target_email = t.assigned_to.email
        target_name  = t.assigned_to.full_name or t.assigned_to.email
    else:
        target_email = "support@terafalk.com"
        target_name  = "TERAFALK Support"

    content = f"""
    <h2 style="margin:0 0 16px;font-size:16px;color:#c5221f">⚠️ SLA-varning</h2>
    <p>Nedanstående ärende riskerar att bryta SLA-tiden.</p>
    <div style="background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:16px">
      <div style="font-weight:700;color:#0047a3">{t.ticket_number} — {t.title}</div>
      <div style="font-size:13px;color:#555;margin-top:8px">
        Kund: {t.customer.name if t.customer else '—'}<br>
        Prioritet: {PRIORITY_LABELS.get(t.priority, t.priority)}<br>
        SLA-tid: {t.sla_due_at.strftime('%Y-%m-%d %H:%M') if t.sla_due_at else '—'}
      </div>
    </div>
    """
    await _send(target_email, target_name, f"[SLA-VARNING] {t.ticket_number}: {t.title}", _base_html(content))
