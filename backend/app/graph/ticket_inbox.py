"""
Poller för inkommande e-post till support@terafalk.com via Microsoft Graph.

Körs av APScheduler var 2:a minut. Kräver Mail.Read (Application) på
samma App Registration som används för Mail.Send.

Logik:
  - Hämta olästa mejl i inkorg
  - Om ämnesraden innehåller [TFxxxxxxxx-xxxx] → lägg till svar på befintligt ärende
  - Annars → skapa nytt ärende (kunden matchas på avsändarens e-post)
  - Markera varje bearbetat mejl som läst
"""

import logging
import re
import uuid

import httpx

from app.core import app_settings

logger = logging.getLogger(__name__)

TICKET_REF_RE = re.compile(r"\[?(TF\d{8}-\d{4})\]?", re.IGNORECASE)
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


async def _get_token() -> str:
    from app.graph.sender import _get_token as _base_get_token
    return await _base_get_token()


async def poll_support_inbox() -> None:
    """Huvudfunktion — körs av scheduler."""
    if not app_settings.get("graph_tenant_id"):
        return

    mailbox = app_settings.get("support_inbox") or "support@terafalk.com"

    try:
        token = await _get_token()
        await _process_unread_messages(token, mailbox)
    except Exception as exc:
        logger.warning("Fel vid e-postpolling: %s", exc)


async def _process_unread_messages(token: str, mailbox: str) -> None:
    from app.db.database import AsyncSessionLocal

    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"{GRAPH_BASE}/users/{mailbox}/mailFolders/Inbox/messages"
        "?$filter=isRead eq false"
        "&$select=id,subject,from,body,receivedDateTime"
        "&$top=20"
        "&$orderby=receivedDateTime asc"
    )

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=headers)
        if r.status_code == 403:
            logger.warning("Mail.Read saknas — lägg till behörigheten i Azure")
            return
        r.raise_for_status()
        messages = r.json().get("value", [])

    async with AsyncSessionLocal() as db:
        for msg in messages:
            try:
                await _handle_message(db, msg, headers)
            except Exception as e:
                logger.warning("Kunde inte bearbeta meddelande %s: %s", msg.get("id"), e)

        await db.commit()

    # Markera alla som lästa
    async with httpx.AsyncClient(timeout=20) as client:
        for msg in messages:
            try:
                await client.patch(
                    f"{GRAPH_BASE}/users/{mailbox}/messages/{msg['id']}",
                    json={"isRead": True},
                    headers=headers,
                )
            except Exception:
                pass


async def _handle_message(db, raw_msg: dict, headers: dict) -> None:
    from app.db.models import Customer, ProcessedEmail, Ticket, TicketMessage, TicketHistory
    from sqlalchemy import select

    graph_id   = raw_msg["id"]
    subject    = raw_msg.get("subject") or "(Inget ämne)"
    sender_obj = raw_msg.get("from", {}).get("emailAddress", {})
    sender_email = sender_obj.get("address", "").lower()
    sender_name  = sender_obj.get("name", sender_email)
    # Sanera inkommande HTML — den lagras och renderas i portalen (XSS-skydd).
    from app.core.html_sanitize import sanitize_html
    body_html    = sanitize_html(raw_msg.get("body", {}).get("content", ""))

    # Undvik att bearbeta samma mejl två gånger.
    # Kontrollera ProcessedEmail-tabellen — den överlever ärendeborttag till skillnad från TicketMessage.
    already_processed = await db.get(ProcessedEmail, graph_id)
    if already_processed:
        return

    # Markera som bearbetad direkt — sker oavsett vad som händer nedanför
    db.add(ProcessedEmail(email_message_id=graph_id))

    # Kolla om detta är ett svar på befintligt ärende
    forced_title: str | None = None
    ref_match = TICKET_REF_RE.search(subject)
    if ref_match:
        ref_number = ref_match.group(1).upper()
        ticket = await db.scalar(
            select(Ticket).where(Ticket.ticket_number == ref_number)
        )
        if ticket and ticket.status != "closed":
            await _add_email_reply(db, ticket, sender_email, sender_name, body_html, graph_id)
            return
        elif ticket and ticket.status == "closed":
            # Stängda ärenden är slutgiltiga — svar skapar ett nytt uppföljningsärende.
            logger.info("Svar på stängt ärende %s → skapar uppföljningsärende", ref_number)
            forced_title = f"Uppföljning på {ref_number}: {_clean_subject(subject)}"
            # falla igenom till nyskapande nedan
        elif not ticket:
            # Ärendet finns inte längre (raderat) — skippa utan att skapa nytt
            logger.info("Ignorerar mail med referens till raderat ärende %s", ref_number)
            return

    # Nytt ärende — matcha kund i prioritetsordning:
    # 1. Exakt e-postmatch på CustomerContact.email
    # 2. Domänmatchning (@foretag.se → kund med kontakt på samma domän)
    # 3. Okänd kund (catch-all)
    from app.db.models import CustomerContact, TicketContact
    matched_contact: "CustomerContact | None" = None

    matched_contact = await db.scalar(
        select(CustomerContact).where(
            CustomerContact.email.ilike(sender_email),
            CustomerContact.is_active == True,
        )
    )
    customer = None
    if matched_contact:
        customer = await db.get(Customer, matched_contact.customer_id)

    if not customer:
        # Domänmatchning mot kontaktpersoner med samma domän
        domain = sender_email.split("@")[-1] if "@" in sender_email else ""
        if domain:
            dom_contact = await db.scalar(
                select(CustomerContact).where(
                    CustomerContact.email.ilike(f"%@{domain}"),
                    CustomerContact.is_active == True,
                ).order_by(CustomerContact.created_at)
            )
            if dom_contact:
                customer = await db.get(Customer, dom_contact.customer_id)
                matched_contact = dom_contact

    if not customer:
        customer = await _get_or_create_extern_customer(db)
        logger.info("Inkommande e-post från okänd avsändare %s → Okänd kund", sender_email)
    else:
        logger.info("Inkommande e-post %s matchad mot kund: %s", sender_email, customer.name)

    ticket_number = await _generate_number(db)
    from app.core.sla import sla_due_dates
    response_due, resolution_due = await sla_due_dates(db, "medium")

    ticket = Ticket(
        id=str(uuid.uuid4()),
        ticket_number=ticket_number,
        customer_id=customer.id,
        type="incident",
        priority="medium",
        status="new",
        title=forced_title or _clean_subject(subject),
        description=body_html,
        source="email",
        source_email=sender_email,
        sla_due_at=resolution_due,
        first_response_due_at=response_due,
    )
    db.add(ticket)
    await db.flush()

    db.add(TicketHistory(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        field_changed="status",
        old_value=None,
        new_value="new",
    ))

    # Lägg automatiskt till matchad kontaktperson som mottagare på ärendet
    if matched_contact:
        db.add(TicketContact(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            contact_id=matched_contact.id,
        ))

    db.add(TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        author_name=sender_name,
        author_email=sender_email,
        body=body_html,
        source="email",
        email_message_id=graph_id,
    ))

    logger.info("Nytt ärende från e-post: %s (%s)", ticket_number, sender_email)

    # Bekräftelsemejl
    try:
        from app.graph.ticket_mailer import send_ticket_created
        await send_ticket_created(ticket_number, ticket.title, sender_email, sender_name, ticket_id=ticket.id)
    except Exception as e:
        logger.warning("Kunde inte skicka bekräftelsemejl för %s: %s", ticket_number, e)


async def _add_email_reply(
    db, ticket, sender_email: str, sender_name: str, body_html: str, graph_id: str
) -> None:
    from app.db.models import TicketMessage, TicketHistory

    db.add(TicketMessage(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        author_email=sender_email,
        author_name=sender_name,
        body=body_html,
        source="email",
        email_message_id=graph_id,
    ))

    # Kund svarar → öppna ärendet igen (löst men ej stängt; stängda hanteras innan denna funktion).
    if ticket.status in ("pending_customer", "resolved"):
        old_status = ticket.status
        ticket.status = "in_progress"
        ticket.resolved_at = None
        db.add(TicketHistory(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            field_changed="status",
            old_value=old_status,
            new_value="in_progress",
        ))

    logger.info("E-postsvar på ärende %s från %s", ticket.ticket_number, sender_email)


async def _generate_number(db) -> str:
    from app.core.ticket_numbers import generate_ticket_number
    return await generate_ticket_number(db)


async def _get_or_create_extern_customer(db) -> "Customer":  # noqa: F821
    from app.db.models import Customer
    from sqlalchemy import select

    customer = await db.scalar(
        select(Customer).where(Customer.name == "Okänd kund", Customer.is_active == True)
    )
    if not customer:
        customer = Customer(
            id=str(uuid.uuid4()),
            name="Okänd kund",
            contact_name="Okänd avsändare",
            city="",
            is_active=True,
        )
        db.add(customer)
        await db.flush()
        logger.info("Skapade generisk Extern-kund för okända avsändare")
    return customer


def _clean_subject(subject: str) -> str:
    """Ta bort Re:/Fwd: och ärendenummertaggar från ämnesraden."""
    s = re.sub(r"^(Re|Fwd|Fw|SV|VS):\s*", "", subject, flags=re.IGNORECASE).strip()
    s = TICKET_REF_RE.sub("", s).strip(" -[]")
    return s or "(Inget ämne)"


