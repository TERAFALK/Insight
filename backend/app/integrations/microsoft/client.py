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

        # Licenser
        sku_ignore = {
            "FLOW_FREE", "POWER_BI_STANDARD", "TEAMS_EXPLORATORY",
            "WINDOWS_STORE", "DEVELOPERPACK_E5",
        }
        licenses = [
            {
                "name": _friendly_sku(s.get("skuPartNumber", "")),
                "sku": s.get("skuPartNumber", ""),
                "total": s.get("prepaidUnits", {}).get("enabled", 0),
                "assigned": s.get("consumedUnits", 0),
            }
            for s in (licenses_raw.get("value") or [])
            if s.get("skuPartNumber") not in sku_ignore
            and s.get("capabilityStatus") == "Enabled"
        ]

        # Användare
        users = users_raw.get("value") or []
        total_users = users_raw.get("@odata.count") or len(users)
        enabled_users = sum(1 for u in users if u.get("accountEnabled"))

        # MFA
        mfa_regs = mfa_raw.get("value") or []
        mfa_capable = sum(1 for u in mfa_regs if u.get("isMfaCapable") or u.get("isMfaRegistered"))
        mfa_registered = sum(1 for u in mfa_regs if u.get("isMfaRegistered"))

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
            "licenses": licenses,
            "mfa_capable": mfa_capable,
            "mfa_registered": mfa_registered,
            "mfa_total": len(mfa_regs),
            "secure_score": secure_score,
            "secure_score_max": secure_score_max,
        }

    async def _fetch_licenses(self, client):
        return await self._get(client, "/subscribedSkus")

    async def _fetch_users(self, client):
        return await self._get(client, "/users", params={
            "$count": "true",
            "$select": "id,accountEnabled",
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
    "SPE_E3": "Microsoft 365 E3",
    "SPE_E5": "Microsoft 365 E5",
    "O365_BUSINESS_PREMIUM": "Microsoft 365 Business Premium",
    "O365_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic",
    "SMB_BUSINESS_PREMIUM": "Microsoft 365 Business Premium",
    "SMB_BUSINESS": "Microsoft 365 Apps for Business",
    "ENTERPRISEPACK": "Office 365 E3",
    "ENTERPRISEPREMIUM": "Office 365 E5",
    "EXCHANGESTANDARD": "Exchange Online (Plan 1)",
    "EXCHANGEENTERPRISE": "Exchange Online (Plan 2)",
    "EMS": "Enterprise Mobility + Security E3",
    "EMSPREMIUM": "Enterprise Mobility + Security E5",
    "AAD_PREMIUM": "Azure AD Premium P1",
    "AAD_PREMIUM_P2": "Azure AD Premium P2",
    "INTUNE_A": "Microsoft Intune",
    "PROJECTPREMIUM": "Project Plan 5",
    "VISIOCLIENT": "Visio Plan 2",
    "POWER_BI_PRO": "Power BI Pro",
    "TEAMS_ESSENTIALS": "Microsoft Teams Essentials",
    "Microsoft_Teams_Rooms_Basic": "Teams Rooms Basic",
}


def _friendly_sku(sku: str) -> str:
    return _SKU_NAMES.get(sku, sku.replace("_", " ").title())
