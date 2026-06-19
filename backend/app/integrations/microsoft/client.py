"""
Microsoft 365-integration.

Kommande funktionalitet:
- Licensöversikt (antal aktiva, tillgängliga, utgångna)
- MFA-status per användare
- Secure Score
- Senaste inloggningar och säkerhetsvarningar

Kräver: App Registration i kundens tenant med delegerad behörighet
(eller admin consent för application permissions).

Endpoints som kommer användas:
- GET /v1.0/subscribedSkus              → licenser
- GET /v1.0/reports/getM365AppUserDetail → appanvändning
- GET /v1.0/security/secureScores       → säkerhetspoäng
- GET /v1.0/users?$select=displayName,userPrincipalName,assignedLicenses
"""

from dataclasses import dataclass


@dataclass
class M365Summary:
    total_licenses: int
    active_users: int
    mfa_enabled_count: int
    mfa_disabled_count: int
    secure_score: float | None
    secure_score_max: float | None


async def get_m365_summary(tenant_id: str, client_id: str, client_secret: str) -> M365Summary:
    """
    Hämtar Microsoft 365-sammanfattning för en kund-tenant.
    Inte implementerat än — returnerar placeholder.
    """
    raise NotImplementedError("Microsoft 365-integration kommer i nästa version")
