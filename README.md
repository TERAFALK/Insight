# Insight

**Managed Network Portal** — ett internt verktyg för att hantera kunder, övervaka nätverksintegrationer och automatiskt generera och skicka månadsrapporter.

---

## Funktioner

- **Kundhantering** — Lägg till, redigera och ta bort kunder med kontaktuppgifter
- **Integrationer** — Koppla och verifiera UniFi, Microsoft 365, Acronis och Cloudfactory per kund
- **Live-data** — Realtidsöverblick av WAN-status, ISP-mått och enheter per kund
- **Rapporter** — Automatiska PDF-månadsrapporter via Microsoft Graph, manuell generering per kund
- **Schemaläggning** — Konfigurerbar cron-trigger (standard: 1:a varje månad, 08:00)

---

## Stack

| Lager | Teknik |
|---|---|
| Frontend | Vanilla JS · Nginx Alpine |
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2 (async) |
| Databas | PostgreSQL 16 |
| PDF | WeasyPrint · Jinja2 |
| E-post | Microsoft Graph API (OAuth2) |
| Auth | JWT (Bearer token) · bcrypt |
| Kryptering | Fernet AES-256 (API-nycklar i vila) |
| Deploy | Docker Compose |

---

## Kom igång

### 1. Klona och konfigurera

```bash
git clone https://github.com/terafalk/insight.git
cd insight
cp .env.example .env
```

Fyll i `.env`:

```env
POSTGRES_PASSWORD=välj-ett-starkt-lösenord
SECRET_KEY=slumpmässig-jwt-nyckel-minst-32-tecken
ENCRYPTION_KEY=fernet-nyckel-genereras-med-python-nedan

# Microsoft Graph (krävs för e-postutskick)
GRAPH_TENANT_ID=...
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...
GRAPH_SENDER=noreply@ditt-domain.se

# Första admin-användaren
FIRST_ADMIN_EMAIL=admin@ditt-domain.se
FIRST_ADMIN_PASSWORD=byt-detta
```

Generera en Fernet-nyckel:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Starta

```bash
docker compose up -d
```

Appen är sedan tillgänglig på `http://localhost:8080`.

### 3. Logga in

Använd de uppgifter du satte i `FIRST_ADMIN_EMAIL` och `FIRST_ADMIN_PASSWORD`.

---

## Konfiguration av rapportschema

Schemaläggningen styrs via miljövariabler i `.env`:

```env
REPORT_SCHEDULE_DAY=1        # Dag i månaden (1 = första)
REPORT_SCHEDULE_HOUR=8       # Timme (24h, Europe/Stockholm)
REPORT_SCHEDULE_MINUTE=0     # Minut
```

Rapporter kan också genereras manuellt per kund via **Kunder → välj kund → Generera rapport**.

---

## Integrationer

| Integration | Status | Krävs |
|---|---|---|
| UniFi (Fabric) | ✅ Implementerad | API-nyckel från kundens Fabric |
| Microsoft 365 | 🔜 Planerad | Tenant ID, Client ID, Client Secret |
| Acronis Backup | 🔜 Planerad | API-nyckel |
| Cloudfactory | 🔜 Planerad | API-nyckel |

Alla API-nycklar krypteras med AES-256 (Fernet) i databasen och dekrypteras enbart i minnet vid anrop.

---

## Microsoft Graph — förutsättningar

För att e-postutskick ska fungera krävs en App Registration i Azure Entra ID med:

- Permission: `Mail.Send` (Application)
- Godkänd av en Global Admin (admin consent)

---

## Projektstruktur

```
insight/
├── backend/
│   └── app/
│       ├── api/           # REST-endpoints
│       ├── core/          # Config, säkerhet, scheduler
│       ├── db/            # SQLAlchemy-modeller, migrering, seed
│       ├── integrations/  # Adaptrar per tjänst
│       ├── reports/       # PDF-generering och rapport-runner
│       └── graph/         # Microsoft Graph e-post
├── frontend/
│   ├── index.html         # Single-page app (Vanilla JS)
│   └── nginx.conf
└── docker-compose.yml
```

---

## Licens

Intern mjukvara — © TERAFALK AB. Ej för distribution.
