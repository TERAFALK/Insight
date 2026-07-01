"""Databasmodeller för Insight."""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """Användare i portalen — antingen admin eller kundanvändare."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, default="", server_default="")
    role: Mapped[str] = mapped_column(String, default="admin", server_default="admin")  # "admin" | "customer"
    customer_id: Mapped[str | None] = mapped_column(String, ForeignKey("customers.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Customer(Base):
    """En kund med Managed Network-avtal."""

    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    contact_name: Mapped[str] = mapped_column(String, default="")
    city: Mapped[str] = mapped_column(String, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationer
    credentials: Mapped[list["IntegrationCredential"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    reports: Mapped[list["Report"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    contacts: Mapped[list["CustomerContact"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class CustomerContact(Base):
    """Kontaktperson på en kund som kan ta emot notifieringar."""

    __tablename__ = "customer_contacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    customer_id: Mapped[str] = mapped_column(
        String, ForeignKey("customers.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    receives_reports: Mapped[bool] = mapped_column(Boolean, default=False)
    has_portal_access: Mapped[bool] = mapped_column(Boolean, default=False)
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    customer: Mapped["Customer"] = relationship(back_populates="contacts")
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])


class IntegrationCredential(Base):
    """Krypterade credentials för en integration per kund."""

    __tablename__ = "integration_credentials"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    customer_id: Mapped[str] = mapped_column(
        String, ForeignKey("customers.id"), nullable=False, index=True
    )
    integration_type: Mapped[str] = mapped_column(String, nullable=False)

    # Alla fält krypterade med AES via app/core/security.py
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    customer: Mapped["Customer"] = relationship(back_populates="credentials")


class SystemSetting(Base):
    """Nyckel-värde-tabell för persistenta systeminställningar (t.ex. rapportschema)."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class ProcessedEmail(Base):
    """Spårar Graph-meddelandeid som redan bearbetats av inbox-pollern.
    Raderas aldrig — överlevde ärendeborttag — så att mailen inte återskapar ärenden."""

    __tablename__ = "processed_emails"

    email_message_id: Mapped[str] = mapped_column(String, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Report(Base):
    """En genererad och skickad månadsrapport per kund."""

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    customer_id: Mapped[str] = mapped_column(
        String, ForeignKey("customers.id"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String, nullable=False)  # "2026-06"
    pdf_path: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    send_status: Mapped[str] = mapped_column(String, default="pending")  # pending|sent|bounced|error
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Snapshot av data som ingick i rapporten (JSON)
    data_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)

    customer: Mapped["Customer"] = relationship(back_populates="reports")


class OrderPhaseTemplate(Base):
    """Konfigurerbar fasmall för order- eller projektfaser."""

    __tablename__ = "order_phase_templates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_type: Mapped[str] = mapped_column(String, nullable=False)  # "order" | "project"
    name: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    orders: Mapped[list["Order"]] = relationship(back_populates="current_phase")


class Order(Base):
    """En order eller ett projekt kopplat till en kund."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    customer_id: Mapped[str] = mapped_column(
        String, ForeignKey("customers.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String, nullable=False)  # "order" | "project"
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_phase_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("order_phase_templates.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="active")  # active|completed|cancelled
    assigned_to_user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped["Customer"] = relationship()
    current_phase: Mapped["OrderPhaseTemplate | None"] = relationship(back_populates="orders")
    assigned_to: Mapped["User | None"] = relationship(foreign_keys=[assigned_to_user_id])
    documents: Mapped[list["OrderDocument"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["ProjectTask"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    time_entries: Mapped[list["TimeEntry"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    contacts: Mapped[list["OrderContact"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderDocument(Base):
    """Uppladdade dokument kopplade till en order/projekt."""

    __tablename__ = "order_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(
        String, ForeignKey("orders.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)  # filnamn på disk
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, default="application/octet-stream")
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    order: Mapped["Order"] = relationship(back_populates="documents")


class ProjectTask(Base):
    """Gantt-uppgift inom ett projekt."""

    __tablename__ = "project_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(
        String, ForeignKey("orders.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    order: Mapped["Order"] = relationship(back_populates="tasks")


class TimeEntry(Base):
    """Registrerad arbetstid på ett projekt."""

    __tablename__ = "time_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(
        String, ForeignKey("orders.id"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billed_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worked_at: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    order: Mapped["Order"] = relationship(back_populates="time_entries")


# ─────────────────────────────────────────────
# Ärendehantering (ITIL)
# ─────────────────────────────────────────────

class TicketCategory(Base):
    """Hierarkisk kategori för ärenden (kategori → underkategori)."""

    __tablename__ = "ticket_categories"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ticket_categories.id"), nullable=True
    )
    color: Mapped[str] = mapped_column(String, default="#6b7280")
    icon: Mapped[str] = mapped_column(String, default="ti-tag")
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    children: Mapped[list["TicketCategory"]] = relationship(
        "TicketCategory", back_populates="parent"
    )
    parent: Mapped["TicketCategory | None"] = relationship(
        "TicketCategory", back_populates="children", remote_side="TicketCategory.id"
    )


class TicketTag(Base):
    """Fri etikett som kan sättas på ärenden (utöver kategori)."""

    __tablename__ = "ticket_tags"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String, default="#6b7280")


class TicketTagLink(Base):
    """Koppling ärende ↔ tagg (many-to-many)."""

    __tablename__ = "ticket_tag_links"

    ticket_id: Mapped[str] = mapped_column(String, ForeignKey("tickets.id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String, ForeignKey("ticket_tags.id"), primary_key=True)


class TicketCounter(Base):
    """Atomär daglig löpnummerräknare för ärendenummer (race-säker)."""

    __tablename__ = "ticket_counters"

    day: Mapped[str] = mapped_column(String, primary_key=True)  # "20260701"
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TicketSlaPolicy(Base):
    """SLA-tider per prioritet."""

    __tablename__ = "ticket_sla_policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[str] = mapped_column(String, nullable=False)  # critical|high|medium|low
    response_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class Ticket(Base):
    """Ett supportärende."""

    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_number: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(
        String, ForeignKey("customers.id"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )
    assigned_to_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )
    # ITIL-typ
    type: Mapped[str] = mapped_column(String, default="incident")
    # new|open|in_progress|pending_customer|resolved|closed|cancelled
    status: Mapped[str] = mapped_column(String, default="new", index=True)
    # critical|high|medium|low
    priority: Mapped[str] = mapped_column(String, default="medium")
    category_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ticket_categories.id"), nullable=True
    )
    subcategory_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ticket_categories.id"), nullable=True
    )
    # Sammanslagning: om satt är detta ärende ett barn som mergats in i parent-ärendet.
    parent_ticket_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("tickets.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    # E-post-källan (om ärendet skapades via e-post)
    source_email: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, default="portal")  # portal|email
    # SLA — resolution (sla_due_at) och first response (first_response_due_at)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_sla_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Kundnöjdhet (CSAT) — sätts av kunden på ett löst/stängt ärende
    csat_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1–5
    csat_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    csat_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped["Customer"] = relationship()
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_user_id])
    assigned_to: Mapped["User | None"] = relationship(foreign_keys=[assigned_to_user_id])
    category: Mapped["TicketCategory | None"] = relationship(foreign_keys=[category_id])
    subcategory: Mapped["TicketCategory | None"] = relationship(foreign_keys=[subcategory_id])
    parent: Mapped["Ticket | None"] = relationship(
        "Ticket", remote_side="Ticket.id", foreign_keys=[parent_ticket_id],
        back_populates="merged_children",
    )
    merged_children: Mapped[list["Ticket"]] = relationship(
        "Ticket", foreign_keys="Ticket.parent_ticket_id", back_populates="parent",
    )
    tags: Mapped[list["TicketTag"]] = relationship(secondary="ticket_tag_links", viewonly=True)
    messages: Mapped[list["TicketMessage"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketMessage.created_at"
    )
    attachments: Mapped[list["TicketAttachment"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    history: Mapped[list["TicketHistory"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketHistory.changed_at"
    )
    contacts: Mapped[list["TicketContact"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    time_entries: Mapped[list["TicketTimeEntry"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketTimeEntry.worked_at"
    )


class TicketMessage(Base):
    """Ett meddelande/svar i ett ärende."""

    __tablename__ = "ticket_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String, ForeignKey("tickets.id"), nullable=False, index=True
    )
    author_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )
    # Om mailet kom in via e-post och avsändaren inte har ett konto
    author_email: Mapped[str | None] = mapped_column(String, nullable=True)
    author_name: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)  # intern notering
    source: Mapped[str] = mapped_column(String, default="portal")  # portal|email
    # Graph message-id för att undvika dubbletter vid polling
    email_message_id: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="messages")
    author: Mapped["User | None"] = relationship()
    attachments: Mapped[list["TicketAttachment"]] = relationship(
        back_populates="message", foreign_keys="TicketAttachment.message_id"
    )


class TicketAttachment(Base):
    """Bifogad fil på ett ärende eller meddelande."""

    __tablename__ = "ticket_attachments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String, ForeignKey("tickets.id"), nullable=False, index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ticket_messages.id"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, default="application/octet-stream")
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="attachments")
    message: Mapped["TicketMessage | None"] = relationship(
        back_populates="attachments", foreign_keys=[message_id]
    )


class TicketHistory(Base):
    """Ändringslogg för ett ärende."""

    __tablename__ = "ticket_history"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String, ForeignKey("tickets.id"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    field_changed: Mapped[str] = mapped_column(String, nullable=False)
    old_value: Mapped[str | None] = mapped_column(String, nullable=True)
    new_value: Mapped[str | None] = mapped_column(String, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="history")


class TicketContact(Base):
    """Kontaktperson kopplad till ett ärende som ska ta emot notifieringar."""

    __tablename__ = "ticket_contacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String, ForeignKey("tickets.id"), nullable=False, index=True
    )
    contact_id: Mapped[str] = mapped_column(
        String, ForeignKey("customer_contacts.id"), nullable=False
    )

    ticket: Mapped["Ticket"] = relationship(back_populates="contacts")
    contact: Mapped["CustomerContact"] = relationship()


class TicketTimeEntry(Base):
    """Registrerad arbetstid på ett ärende."""

    __tablename__ = "ticket_time_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String, ForeignKey("tickets.id"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billed_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worked_at: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="time_entries")
    user: Mapped["User | None"] = relationship()


# ─────────────────────────────────────────────
# Ordrar/projekt – kontakter & tilldelning
# ─────────────────────────────────────────────

class OrderContact(Base):
    """Kontaktperson kopplad till en order/projekt (mottagare av notiser)."""

    __tablename__ = "order_contacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), nullable=False, index=True)
    contact_id: Mapped[str] = mapped_column(String, ForeignKey("customer_contacts.id"), nullable=False)

    order: Mapped["Order"] = relationship(back_populates="contacts")
    contact: Mapped["CustomerContact"] = relationship()


# ─────────────────────────────────────────────
# Notifikationsinställningar
# ─────────────────────────────────────────────

class CannedResponse(Base):
    """Återanvändbart standardsvar för ärendehanteringen."""

    __tablename__ = "canned_responses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Spårar känsliga admin-åtgärder (kund-, användar- och inställningsändringar)."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # Ingen FK — loggen ska överleva även om användaren raderas (e-post denormaliseras)
    actor_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_email: Mapped[str] = mapped_column(String, default="")
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)  # t.ex. "customer.create"
    entity_type: Mapped[str] = mapped_column(String, default="")
    entity_id: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class NotificationSetting(Base):
    """Konfigurerbar e-postnotifiering per händelsetyp."""

    __tablename__ = "notification_settings"

    event_type: Mapped[str] = mapped_column(String, primary_key=True)
    # Händelsebeskrivning (visas i UI)
    label: Mapped[str] = mapped_column(String, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_assigned: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    # Valfri override-adress för intern notis (tomt = använd support_inbox)
    internal_email: Mapped[str] = mapped_column(String, default="")
