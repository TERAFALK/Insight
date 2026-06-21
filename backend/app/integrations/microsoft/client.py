"""
Microsoft Graph API-klient med client credentials flow.
Hämtar licensöversikt, MFA-registreringsstatus och Secure Score för en kunds tenant.
"""

import logging
import httpx

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._token:
            return self._token
        r = await client.post(
            f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
        token = await self._get_token(client)
        r = await client.get(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"},
            params=params,
        )
        r.raise_for_status()
        return r.json()

    async def fetch_all(self) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            licenses_raw, users_raw, mfa_raw, score_raw = await _gather(
                self._fetch_licenses(client),
                self._fetch_users(client),
                self._fetch_mfa(client),
                self._fetch_secure_score(client),
            )

        # Licenser — bygg skuId→{name,sku} karta för användarmappning
        sku_ignore = {
            "FLOW_FREE", "POWER_BI_STANDARD", "TEAMS_EXPLORATORY",
            "WINDOWS_STORE", "DEVELOPERPACK_E5",
        }
        sku_id_map: dict[str, str] = {}  # skuId (guid) → friendly name
        licenses = []
        for s in (licenses_raw.get("value") or []):
            part = s.get("skuPartNumber", "")
            sku_id = s.get("skuId", "")
            friendly = _friendly_sku(part)
            if sku_id:
                sku_id_map[sku_id] = friendly
            if part in sku_ignore or s.get("capabilityStatus") != "Enabled":
                continue
            licenses.append({
                "name": friendly,
                "sku": part,
                "sku_id": sku_id,
                "total": s.get("prepaidUnits", {}).get("enabled", 0),
                "assigned": s.get("consumedUnits", 0),
            })

        # Användare — inkl. tilldelade licenser
        users = users_raw.get("value") or []
        total_users = users_raw.get("@odata.count") or len(users)
        enabled_users = sum(1 for u in users if u.get("accountEnabled"))
        user_list = [
            {
                "name": u.get("displayName") or u.get("userPrincipalName", ""),
                "email": u.get("mail") or u.get("userPrincipalName", ""),
                "title": u.get("jobTitle") or "",
                "enabled": u.get("accountEnabled", False),
                "licenses": [
                    sku_id_map.get(lic.get("skuId", ""), lic.get("skuId", ""))
                    for lic in (u.get("assignedLicenses") or [])
                    if lic.get("skuId") in sku_id_map
                ],
            }
            for u in users
        ]

        # MFA — authenticationMethods kräver UserAuthenticationMethod.Read.All
        # Alternativ: räkna via strongAuthenticationRequirements i users (äldre API)
        mfa_regs = mfa_raw.get("value") or []
        mfa_registered = sum(1 for u in mfa_regs if u.get("isMfaRegistered"))
        mfa_total = len(mfa_regs)

        # Bygg UPN→isMfaRegistered karta för användartabellen
        mfa_by_upn: dict[str, bool] = {
            u.get("userPrincipalName", "").lower(): u.get("isMfaRegistered", False)
            for u in mfa_regs
        }
        if mfa_by_upn:
            for u in user_list:
                upn = u["email"].lower()
                if upn in mfa_by_upn:
                    u["mfa"] = mfa_by_upn[upn]

        # Secure Score
        scores = score_raw.get("value") or []
        secure_score = None
        secure_score_max = None
        if scores:
            sc = scores[0]
            secure_score = sc.get("currentScore")
            secure_score_max = sc.get("maxScore")

        return {
            "integration": "microsoft",
            "available": True,
            "total_users": total_users,
            "enabled_users": enabled_users,
            "users": user_list,
            "licenses": licenses,
            "mfa_registered": mfa_registered,
            "mfa_total": mfa_total,
            "mfa_available": mfa_total > 0,
            "secure_score": secure_score,
            "secure_score_max": secure_score_max,
        }

    async def _fetch_licenses(self, client):
        return await self._get(client, "/subscribedSkus")

    async def _fetch_users(self, client):
        return await self._get(client, "/users", params={
            "$count": "true",
            "$select": "id,displayName,userPrincipalName,mail,jobTitle,accountEnabled,assignedLicenses",
            "$top": "999",
        })

    async def _fetch_mfa(self, client):
        try:
            return await self._get(client, "/reports/authenticationMethods/userRegistrationDetails", params={"$top": "999"})
        except Exception as e:
            logger.warning("MFA-data ej tillgänglig: %s", e)
            return {"value": []}

    async def _fetch_secure_score(self, client):
        try:
            return await self._get(client, "/security/secureScores", params={"$top": "1"})
        except Exception as e:
            logger.warning("Secure Score ej tillgänglig: %s", e)
            return {"value": []}


async def _gather(*coros):
    import asyncio
    return await asyncio.gather(*coros, return_exceptions=False)


_SKU_NAMES = {
    # Microsoft 365 Business
    "O365_BUSINESS_PREMIUM": "Microsoft 365 Business Premium",
    "O365_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic",
    "O365_BUSINESS": "Microsoft 365 Apps for Business",
    "SMB_BUSINESS_PREMIUM": "Microsoft 365 Business Premium",
    "SMB_BUSINESS": "Microsoft 365 Apps for Business",
    "SMB_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic",
    "SPB": "Microsoft 365 Business Standard",
    "MICROSOFT_BUSINESS_CENTER": "Microsoft 365 Business Center",
    # Microsoft 365 Enterprise
    "SPE_E3": "Microsoft 365 E3",
    "SPE_E5": "Microsoft 365 E5",
    "SPE_F1": "Microsoft 365 F1",
    "SPE_E3_USGOV_DOD": "Microsoft 365 E3 (Gov)",
    "SPE_E3_USGOV_GCCHIGH": "Microsoft 365 E3 (Gov High)",
    # Office 365
    "ENTERPRISEPACK": "Office 365 E3",
    "ENTERPRISEPREMIUM": "Office 365 E5",
    "STANDARDPACK": "Office 365 E1",
    "DESKLESSPACK": "Office 365 F3",
    "ENTERPRISEPACK_USGOV_DOD": "Office 365 E3 (Gov)",
    # Exchange
    "EXCHANGESTANDARD": "Exchange Online (Plan 1)",
    "EXCHANGEENTERPRISE": "Exchange Online (Plan 2)",
    "EXCHANGEESSENTIALS": "Exchange Online Essentials",
    "EXCHANGE_S_DESKLESS": "Exchange Online Kiosk",
    # Teams
    "TEAMS_ESSENTIALS": "Microsoft Teams Essentials",
    "TEAMS_FREE": "Microsoft Teams (gratis)",
    "TEAMS_EXPLORATORY": "Microsoft Teams Exploratory",
    "Microsoft_Teams_Rooms_Basic": "Teams Rooms Basic",
    "Microsoft_Teams_Rooms_Standard": "Teams Rooms Standard",
    # Security & Compliance
    "EMS": "Enterprise Mobility + Security E3",
    "EMSPREMIUM": "Enterprise Mobility + Security E5",
    "AAD_PREMIUM": "Azure AD Premium P1",
    "AAD_PREMIUM_P2": "Azure AD Premium P2",
    "INTUNE_A": "Microsoft Intune Plan 1",
    "INTUNE_A_D": "Microsoft Intune Plan 2",
    "DEFENDER_ENDPOINT_P1": "Microsoft Defender for Endpoint P1",
    "DEFENDER_ENDPOINT_P2": "Microsoft Defender for Endpoint P2",
    "ATP_ENTERPRISE": "Microsoft Defender for Office 365 P1",
    "THREAT_INTELLIGENCE": "Microsoft Defender for Office 365 P2",
    "INFORMATION_PROTECTION_COMPLIANCE": "Microsoft 365 E5 Compliance",
    # Power Platform
    "POWER_BI_PRO": "Power BI Pro",
    "POWER_BI_PREMIUM_PER_USER": "Power BI Premium Per User",
    "POWER_BI_STANDARD": "Power BI (gratis)",
    "POWERAPPS_PER_USER": "Power Apps per user",
    "FLOW_PER_USER": "Power Automate per user",
    "FLOW_FREE": "Power Automate (gratis)",
    # Project & Visio
    "PROJECTPREMIUM": "Project Plan 5",
    "PROJECTPROFESSIONAL": "Project Plan 3",
    "PROJECTESSENTIALS": "Project Plan 1",
    "VISIOCLIENT": "Visio Plan 2",
    "VISIO_PLAN1_DEPT": "Visio Plan 1",
    # Copilot & AI
    "Microsoft_365_Copilot": "Microsoft 365 Copilot",
    "COPILOT_FOR_MICROSOFT_365": "Microsoft 365 Copilot",
    # Dynamics 365
    "DYN365_ENTERPRISE_SALES": "Dynamics 365 Sales Enterprise",
    "DYN365_SALES_PREMIUM_VIRAL": "Dynamics 365 Sales Premium (Trial)",
    "DYN365_TEAM_MEMBERS": "Dynamics 365 Team Members",
    "DYN365_BUSINESS_CENTRAL_ESSENTIAL": "Dynamics 365 Business Central Essential",
    "DYN365_BUSINESS_CENTRAL_PREMIUM": "Dynamics 365 Business Central Premium",
    # Other
    "DEVELOPERPACK_E5": "Microsoft 365 E5 Developer",
    "WINDOWS_STORE": "Microsoft Store for Business",
    "MCOCAP": "Microsoft Teams Phone",
    "MCOSTANDARD": "Skype for Business Online (Plan 2)",
}


def _friendly_sku(sku: str) -> str:
    if sku in _SKU_NAMES:
        return _SKU_NAMES[sku]
    # Fallback: replace underscores, fix casing (keep all-uppercase words as-is)
    parts = sku.replace("_", " ").split()
    formatted = " ".join(p if p.isupper() and len(p) > 2 else p.capitalize() for p in parts)
    return formatted
