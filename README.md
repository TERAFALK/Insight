# TERAFALK Customer Portal

Managed Network-portal för TERAFALK AB. Hanterar kunder, UniFi-nät, automatiska månadsrapporter och kommande integrationer mot Microsoft 365, Acronis och Cloudfactory.

## Snabbstart

### 1. Klona och konfigurera
```bash
git clone https://github.com/TERAFALK/Customer-Portal.git
cd Customer-Portal
cp .env.example .env
```

Öppna `.env` och fyll i:
- `POSTGRES_PASSWORD` — välj ett starkt lösenord
- `SECRET_KEY` — generera med `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `ENCRYPTION_KEY` — generera på samma sätt (annan nyckel!)
- `FIRST_ADMIN_PASSWORD` — ditt första inloggningslösenord

Graph-fälten (`GRAPH_TENANT_ID` etc.) kan lämnas tomma till att börja med — rapporter genereras men skickas inte.

### 2. Starta
```bash
docker compose up -d
```

Portalen är tillgänglig på `http://din-server-ip`

### 3. Logga in
- E-post: värdet du satte i `FIRST_ADMIN_EMAIL` (standard: `admin@terafalk.com`)
- Lösenord: värdet du satte i `FIRST_ADMIN_PASSWORD`

**Byt lösenord direkt efter första inloggning.**

---

## Microsoft Graph — e-postutskick

För att aktivera automatiska rapportutskick via `noreply@terafalk.com`:

1. Gå till [portal.azure.com](https://portal.azure.com) → **Entra ID → App registrations → New registration**
2. Namn: `TERAFALK Portal`, Account type: *Single tenant*
3. **API permissions → Add → Microsoft Graph → Application permissions → Mail.Send → Grant admin consent**
4. **Certificates & secrets → New client secret** → kopiera värdet
5. **Overview** → kopiera *Application (client) ID* och *Directory (tenant) ID*
6. Lägg in värdena i `.env` och kör `docker compose restart backend`

---

## Lägga till en kund

1. Logga in i portalen
2. **Lägg till kund** i sidomenyn
3. Fyll i namn, e-post och ort
4. Gå till kundens Fabric på [unifi.ui.com](https://unifi.ui.com) → Settings → API Keys → skapa en nyckel
5. Klistra in nyckeln i formuläret → spara

Nyckeln krypteras med AES-256 innan den lagras i databasen.

---

## Integrationer (kommande)

| Integration | Status | Vad det ger rapporten |
|---|---|---|
| UniFi | ✅ Aktivt | Enheter, firmware, WAN-uptime, ISP-mått, IPS |
| Microsoft 365 | 🔜 Snart | Licensöversikt, säkerhetspoäng, MFA-status |
| Acronis | 🔜 Snart | Säkerhetskopiestatus per enhet |
| Cloudfactory | 🔜 Snart | Licensdata och tjänststatus |

---

## Rapportschema

Rapporter skickas automatiskt den 1:a varje månad kl 08:00 (Europe/Stockholm). Ändras i `.env`:

```env
REPORT_SCHEDULE_DAY=1
REPORT_SCHEDULE_HOUR=8
REPORT_SCHEDULE_MINUTE=0
```

Manuell körning via portalen: **Rapporter → Kör alla nu**, eller per kund via kundvyn.

---

## Projektstruktur

```
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI-endpoints
│   │   ├── core/         # Config, säkerhet, scheduler
│   │   ├── db/           # Modeller och databas
│   │   ├── graph/        # Microsoft Graph-utskick
│   │   ├── integrations/ # Microsoft, Acronis, Cloudfactory (stubs)
│   │   ├── reports/      # PDF-generering och rapport-runner
│   │   └── unifi/        # UniFi Site Manager API-klient
│   └── requirements.txt
├── frontend/
│   └── index.html        # Single-page portal
├── nginx/
│   └── nginx.conf
├── docker-compose.yml
└── .env.example
```

---

## Säkerhet

- Alla API-nycklar krypteras med AES-256 (Fernet) i databasen
- JWT-tokens med 8h utgångstid
- Lösenord hashas med bcrypt
- `.env` och certifikat är i `.gitignore` — hamnar aldrig i Git
