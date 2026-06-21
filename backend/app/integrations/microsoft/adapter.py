"""Microsoft 365-adapter via Microsoft Graph API (client credentials flow)."""

from app.core.security import decrypt
from app.db.models import IntegrationCredential
from app.integrations.microsoft.client import GraphClient


class MicrosoftIntegration:
    async def verify(self, credential: IntegrationCredential) -> tuple[bool, str]:
        if not (credential.tenant_id and credential.client_id and credential.client_secret):
            return False, "Tenant ID, Client ID och Client Secret krävs"
        try:
            import httpx
            tenant_id = decrypt(credential.tenant_id)
            client_id = decrypt(credential.client_id)
            client_secret = decrypt(credential.client_secret)
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "scope": "https://graph.microsoft.com/.default",
                    },
                )
            if r.status_code == 200:
                return True, "OK"
            body = r.json()
            return False, body.get("error_description") or f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)

    async def fetch_report_data(self, credential: IntegrationCredential) -> dict:
        if not (credential.tenant_id and credential.client_id and credential.client_secret):
            raise ValueError("Microsoft 365-credentials ej fullständiga")
        tenant_id = decrypt(credential.tenant_id)
        client_id = decrypt(credential.client_id)
        client_secret = decrypt(credential.client_secret)
        gc = GraphClient(tenant_id, client_id, client_secret)
        return await gc.fetch_all()
