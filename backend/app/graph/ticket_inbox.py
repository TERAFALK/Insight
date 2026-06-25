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
from datetime import datetime, timezone

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

    mailbox = app_settings.get("graph_sender") or "support@terafalk.com"

    try:
        token = await _get_token()
        await _process_unread_messages(token, mailbox)
    except Exception as exc:
        logger.warning("Fel vid e-postpolling: %s", exc)


async def _process_unread_messages(token: str, mailbox: str) -> None:
    from app.db.database import AsyncSessionLocal
    from app.db.models import Customer, Ticket, TicketMessage, TicketHistory
    from sqlalchemy import select

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
    from app.db.models import Customer, Ticket, TicketMessage, TicketHistory
    from sqlalchemy import select

    graph_id   = raw_msg["id"]
    subject    = raw_msg.get("subject") or "(Inget ämne)"
    sender_obj = raw_msg.get("from", {}).get("emailAddress", {})
    sender_email = sender_obj.get("address", "").lower()
    sender_name  = sender_obj.get("name", sender_email)
    body_html    = raw_msg.get("body", {}).get("content", "")

    # Undvik att bearbeta samma mejl två gånger
    existing_msg = await db.scalar(
        select(TicketMessage).where(TicketMessage.email_message_id == graph_id)
    )
    if existing_msg:
        return

    # Kolla om detta är ett svar på befintligt ärende
    ref_match = TICKET_REF_RE.search(subject)
    if ref_match:
        ticket_number = ref_match.group(1).upper()
        ticket = await db.scalar(
            select(Ticket).where(Ticket.ticket_number == ticket_number)
        )
        if ticket:
            await _add_email_reply(db, ticket, sender_email, sender_name, body_html, graph_id)
            return

    # Nytt ärende — försök matcha kund på e-post (primär kontakt eller kontaktperson)
    from app.db.models import CustomerContact
    customer = await db.scalar(
        select(Customer).where(Customer.contact_email.ilike(sender_email), Customer.is_active == True)
    )
    if not customer:
        # Prova att matcha mot kontaktpersoner
        contact = await db.scalar(
            select(CustomerContact).where(
                CustomerContact.email.ilike(sender_email),
                CustomerContact.is_active == True,
            )
        )
        if contact:
            customer = await db.get(Customer, contact.customer_id)

    if not customer:
        # Skapa ärende på generisk "Extern"-kund
        customer = await _get_or_create_extern_customer(db)
        logger.info("Inkommande e-post från okänd avsändare %s → kopplas till Extern-kund", sender_email)

    ticket_number = await _generate_number(db)
    sla_due = await _default_sla_due(db)

    ticket = Ticket(
        id=str(uuid.uuid4()),
        ticket_number=ticket_number,
        customer_id=customer.id,
        type="incident",
        priority="medium",
        status="new",
        title=_clean_subject(subject),
        description=body_html,
        source="email",
        source_email=sender_email,
        sla_due_at=sla_due,
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
        await send_ticket_created(ticket_number, ticket.title, sender_email, sender_name)
    except Exception:
        pass


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

    # Kund svarar → öppna igen
    if ticket.status == "pending_customer":
        db.add(TicketHistory(
            id=str(uuid.uuid4()),
            ticket_id=ticket.id,
            field_changed="status",
            old_value="pending_customer",
            new_value="in_progress",
        ))
        ticket.status = "in_progress"

    logger.info("E-postsvar på ärende %s från %s", ticket.ticket_number, sender_email)


async def _generate_number(db) -> str:
    from app.db.models import Ticket
    from sqlalchemy import select
    from sqlalchemy import func as sqlfunc

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"TF{today}-"
    result = await db.scalar(
        select(sqlfunc.max(Ticket.ticket_number)).where(
            Ticket.ticket_number.like(f"{prefix}%")
        )
    )
    seq = (int(result.split("-")[-1]) + 1) if result else 1
    return f"{prefix}{seq:04d}"


async def _default_sla_due(db) -> "datetime | None":
    from app.db.models import TicketSlaPolicy
    from sqlalchemy import select
    from datetime import timedelta

    policy = await db.scalar(
        select(TicketSlaPolicy).where(TicketSlaPolicy.priority == "medium")
    )
    if not policy:
        return None
    return datetime.now(timezone.utc) + timedelta(hours=policy.resolution_hours)


async def _get_or_create_extern_customer(db) -> "Customer":
    from app.db.models import Customer
    from sqlalchemy import select

    customer = await db.scalar(
        select(Customer).where(Customer.name == "Extern", Customer.is_active == True)
    )
    if not customer:
        customer = Customer(
            id=str(uuid.uuid4()),
            name="Extern",
            contact_name="Okänd avsändare",
            contact_email="support@terafalk.com",
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


