from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR NOT NULL DEFAULT 'admin'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS customer_id VARCHAR REFERENCES customers(id)",
            # Migrate ticket timestamp columns to TIMESTAMPTZ (idempotent via USING clause)
            "ALTER TABLE tickets ALTER COLUMN sla_due_at TYPE TIMESTAMPTZ USING sla_due_at AT TIME ZONE 'UTC'",
            "ALTER TABLE tickets ALTER COLUMN first_responded_at TYPE TIMESTAMPTZ USING first_responded_at AT TIME ZONE 'UTC'",
            "ALTER TABLE tickets ALTER COLUMN resolved_at TYPE TIMESTAMPTZ USING resolved_at AT TIME ZONE 'UTC'",
            "ALTER TABLE tickets ALTER COLUMN closed_at TYPE TIMESTAMPTZ USING closed_at AT TIME ZONE 'UTC'",
            "ALTER TABLE tickets ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC'",
            "ALTER TABLE tickets ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC'",
            "ALTER TABLE ticket_messages ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC'",
            "ALTER TABLE ticket_attachments ALTER COLUMN uploaded_at TYPE TIMESTAMPTZ USING uploaded_at AT TIME ZONE 'UTC'",
            "ALTER TABLE ticket_history ALTER COLUMN changed_at TYPE TIMESTAMPTZ USING changed_at AT TIME ZONE 'UTC'",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS assigned_to_user_id VARCHAR REFERENCES users(id)",
            "ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS receives_reports BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS has_portal_access BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS user_id VARCHAR REFERENCES users(id)",
            # Den ursprungliga kund-mailposten är ersatt av customer_contacts — ta bort kolumnen helt
            "ALTER TABLE customers DROP COLUMN IF EXISTS contact_email",
            # Tekniker-rollen är borttagen — befintliga tekniker blir administratörer
            "UPDATE users SET role = 'admin' WHERE role = 'technician'",
            # Sammanslagning av ärenden (parent/child)
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS parent_ticket_id VARCHAR REFERENCES tickets(id)",
            # First-response-SLA
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS first_response_due_at TIMESTAMPTZ",
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS response_sla_breached BOOLEAN NOT NULL DEFAULT FALSE",
            # CSAT (kundnöjdhet)
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_score INTEGER",
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_comment TEXT",
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_submitted_at TIMESTAMPTZ",
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_token VARCHAR",
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS csat_survey_sent_at TIMESTAMPTZ",
            # Per-kund rapportschema
            "ALTER TABLE customers ADD COLUMN IF NOT EXISTS report_frequency VARCHAR NOT NULL DEFAULT 'monthly'",
            "ALTER TABLE customers ADD COLUMN IF NOT EXISTS report_day INTEGER NOT NULL DEFAULT 0",
            # En äldre audit_logs-design hade extra NOT NULL-kolumner (t.ex. user_email)
            # som blockerar inserts från den nya modellen. Droppa alla kolumner som
            # inte tillhör den nuvarande modellen (no-op på fräscha installationer).
            "DO $$ DECLARE col text; BEGIN "
            "FOR col IN SELECT column_name FROM information_schema.columns "
            "WHERE table_name='audit_logs' AND column_name NOT IN "
            "('id','actor_user_id','actor_email','action','entity_type','entity_id','summary','created_at') "
            "LOOP EXECUTE 'ALTER TABLE audit_logs DROP COLUMN ' || quote_ident(col); END LOOP; "
            "END $$;",
            # Audit-logg — säkerställ alla kolumner även om tabellen redan fanns
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_user_id VARCHAR",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS actor_email VARCHAR NOT NULL DEFAULT ''",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS action VARCHAR NOT NULL DEFAULT ''",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS entity_type VARCHAR NOT NULL DEFAULT ''",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS entity_id VARCHAR",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS summary VARCHAR NOT NULL DEFAULT ''",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ]:
            await conn.execute(text(stmt))
    await _seed_phase_templates()
    await _seed_ticket_defaults()
    await _seed_notification_settings()


async def _seed_phase_templates() -> None:
    """Skapa standardfaser om tabellen är tom."""
    from app.db.models import OrderPhaseTemplate

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select, func as sqlfunc

        count = await session.scalar(
            select(sqlfunc.count()).select_from(OrderPhaseTemplate)
        )
        if count and count > 0:
            return

        defaults = {
            "order": ["Bekräftad", "Beställd hos leverantör", "Skickad", "Levererad", "Klar"],
            "project": ["Planering", "Pågår", "Delleverans", "Avslutat"],
        }
        import uuid as _uuid_mod
        for order_type, names in defaults.items():
            for pos, name in enumerate(names):
                session.add(
                    OrderPhaseTemplate(
                        id=str(_uuid_mod.uuid4()),
                        order_type=order_type,
                        name=name,
                        position=pos,
                        is_default=(pos == 0),
                    )
                )
        await session.commit()



async def _seed_ticket_defaults() -> None:
    """Skapa standard SLA-policyer och ärendekategorier om de saknas."""
    from app.db.models import TicketSlaPolicy, TicketCategory
    from sqlalchemy import select, func as sqlfunc
    import uuid as _uuid_mod

    async with AsyncSessionLocal() as session:
        sla_count = await session.scalar(
            select(sqlfunc.count()).select_from(TicketSlaPolicy)
        )
        if not sla_count:
            defaults = [
                ("Critical", "critical", 1, 4),
                ("High",     "high",     4, 8),
                ("Medium",   "medium",   8, 24),
                ("Low",      "low",      24, 72),
            ]
            for name, prio, resp, resol in defaults:
                session.add(TicketSlaPolicy(
                    id=str(_uuid_mod.uuid4()),
                    name=name, priority=prio,
                    response_hours=resp, resolution_hours=resol,
                    is_default=True,
                ))

        cat_count = await session.scalar(
            select(sqlfunc.count()).select_from(TicketCategory)
        )
        if not cat_count:
            categories = [
                ("Nätverk",      "#3b82f6", "ti-network"),
                ("Server",       "#8b5cf6", "ti-server"),
                ("Säkerhet",     "#ef4444", "ti-shield"),
                ("E-post",       "#f59e0b", "ti-mail"),
                ("Övrigt",       "#6b7280", "ti-dots"),
            ]
            for name, color, icon in categories:
                session.add(TicketCategory(
                    id=str(_uuid_mod.uuid4()),
                    name=name, color=color, icon=icon,
                ))
        await session.commit()


async def get_db():
    session: AsyncSession = AsyncSessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def _seed_notification_settings() -> None:
    """Skapa standardinställningar för notifikationer om de saknas."""
    from app.db.models import NotificationSetting
    from sqlalchemy import select

    defaults = [
        # event_type, label, enabled, notify_customer, notify_assigned, notify_internal
        ("ticket_created",        "Nytt ärende skapat",              True,  True,  False, False),
        ("ticket_reply_staff",    "Personal svarar på ärende",       True,  True,  False, False),
        ("ticket_reply_customer", "Kund svarar på ärende",           True,  False, True,  False),
        ("ticket_status_changed", "Ärendestatus ändrad",             False, False, True,  False),
        ("ticket_resolved",       "Ärende löst",                     True,  True,  False, False),
        ("ticket_assigned",       "Ärende tilldelat",                True,  False, True,  False),
        ("ticket_mention",        "Nämnd i intern notering",         True,  False, False, False),
        ("ticket_sla_warning",    "SLA-varning",                     True,  False, True,  False),
        ("order_created",         "Ny order/projekt skapad",         True,  True,  False, False),
        ("order_status_changed",  "Order/projekt status ändrad",     True,  True,  True,  False),
        ("order_phase_changed",   "Order/projekt fas ändrad",        True,  True,  False, False),
    ]

    async with AsyncSessionLocal() as session:
        for (event_type, label, enabled, notify_customer, notify_assigned, notify_internal) in defaults:
            existing = await session.get(NotificationSetting, event_type)
            if not existing:
                session.add(NotificationSetting(
                    event_type=event_type,
                    label=label,
                    enabled=enabled,
                    notify_customer=notify_customer,
                    notify_assigned=notify_assigned,
                    notify_internal=notify_internal,
                    internal_email="",
                ))
        await session.commit()
