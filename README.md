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

- Backend (API) körs på port `8000`
- Frontend (portalen) körs på port `8080`

Sätt din egen reverse proxy (nginx, Caddy, Traefik) att peka mot serverns IP på dessa portar: `/` mot port `8080` (frontend) och `/api/` mot port `8000` (backend).

Portalen är tillgänglig på `http://din-server-ip:8080` (eller via din egen reverse proxy/domän).

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

## Integrationer

Alla integrationer — inklusive UniFi — är jämbördiga och valfria per kund. En kund kan ha vilken kombination som helst (bara UniFi, bara Acronis, alla fyra, eller inga alls). Rapporten byggs dynamiskt utifrån vilka integrationer som är **konfigurerade och verifierade** för just den kunden — en integration som inte är verifierad visas aldrig med platshållardata i rapporten.

| Integration | Status | Vad det ger rapporten |
|---|---|---|
| UniFi | ✅ Aktivt | Enheter, firmware, WAN-uptime, ISP-mått, IPS |
| Microsoft 365 | 🔜 Snart | Licensöversikt, säkerhetspoäng, MFA-status |
| Acronis | 🔜 Snart | Säkerhetskopiestatus per enhet |
| Cloudfactory | 🔜 Snart | Licensdata och tjänststatus |

Lägg till eller ta bort en integration för en kund under **Kund → Integrationer**. Samma flöde (API-nyckel/credentials → Spara & verifiera) gäller oavsett integrationstyp.

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
│   │   ├── integrations/ # UniFi, Microsoft, Acronis, Cloudfactory (jämbördiga)
│   │   └── reports/      # PDF-generering och rapport-runner
│   └── requirements.txt
├── frontend/
│   └── index.html        # Single-page portal
├── docker-compose.yml
└── .env.example
```

---

## Säkerhet

- Alla API-nycklar krypteras med AES-256 (Fernet) i databasen
- JWT-tokens med 8h utgångstid
- Lösenord hashas med bcrypt (automatiskt trunkerade vid >72 bytes — bcrypts hårda gräns)
- `.env` och certifikat är i `.gitignore` — hamnar aldrig i Git

---

## Felsökning

**`exec: "uvicorn": executable file not found in $PATH`**
Imagen byggdes med en gammal `requirements.txt` innan uvicorn lades till, eller bygg-cachen är stale. Kör:
```bash
docker compose build --no-cache backend
docker compose up -d
```

**`(trapped) error reading bcrypt version` / `bcrypt has no attribute '__about__'`**
`passlib==1.7.4` försöker läsa ett internt bcrypt-attribut som togs bort i bcrypt 4.1+. Redan löst i `requirements.txt` genom att låsa `bcrypt==4.0.1`. Om felet ändå dyker upp, bygg om utan cache enligt ovan.

**`ValueError: password cannot be longer than 72 bytes`**
bcrypt har en hård gräns på 72 bytes per lösenord. Koden trunkerar nu automatiskt (`app/core/security.py`), så detta ska inte längre kunna inträffa — men håll ändå `FIRST_ADMIN_PASSWORD` under ca 50 tecken för enkelhetens skull.

**Kund går inte att skapa / knappar gör inget**
Tidigare versioner av frontend var en ren visuell demo utan koppling till backend. Från och med denna version är alla vyer databundna mot `/api/...`. Om ett anrop misslyckas visas felmeddelandet i en toast eller som en röd ruta i vyn — öppna webbläsarens devtools-konsol (F12) för fullständig felinformation om något fortfarande inte fungerar.

