"""
Databasmodeller för Insight.

Designade brett för att stödja kommande integrationer:
- Microsoft 365 (tenant-koppling per kund)
- Acronis (backup-status per kund)
- Cloudfactory (licenser per kund)
- UniFi (en API-nyckel per kund/fabric)

Varje kund (Customer) kan ha flera integration_credentials, en per typ.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
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
    """
    En kund med Managed Network-avtal.
    Varje kund har sin egen UniFi Fabric och potentiellt
    kopplingar till Microsoft 365, Acronis och Cloudfactory.
    """

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
    """
    Krypterade credentials för en integration per kund.

    integration_type: "unifi" | "microsoft" | "acronis" | "cloudfactory"

    Fält:
      api_key       — UniFi API-nyckel, Acronis API-nyckel, Cloudfactory-nyckel
      tenant_id     — Microsoft 365 Tenant ID
      client_id     — Microsoft app client ID
      client_secret — Microsoft app client secret
      extra_data    — JSON-sträng för framtida fält utan schemaändring
    """

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
