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
    contact_email: Mapped[str] = mapped_column(String, nullable=False)
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
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped["Customer"] = relationship()
    current_phase: Mapped["OrderPhaseTemplate | None"] = relationship(back_populates="orders")
    documents: Mapped[list["OrderDocument"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["ProjectTask"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    time_entries: Mapped[list["TimeEntry"]] = relationship(
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
