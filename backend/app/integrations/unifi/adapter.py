"""
UniFi-adapter — kopplar UnifiClient till det gemensamma integrationsgränssnittet.

UnifiClient använder httpx.Client (synkront) internt, inte AsyncClient.
fetch_report_data körs därför via asyncio.to_thread så det blockerande
nätverksanropet inte fryser event-loopen för andra samtidiga requests
mot backend — annars skulle EN kunds UniFi-anrop blockera ALLA andra
användare av API:t under tiden det pågår.
"""

import asyncio

import httpx

from app.core.security import decrypt
from app.db.models import IntegrationCredential
from app.integrations.unifi.client import UnifiClient


class UnifiIntegration:
    async def verify(self, credential: IntegrationCredential) -> tuple[bool, str]:
        if not credential.api_key:
            return False, "Ingen API-nyckel sparad"
        key = decrypt(credential.api_key)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.ui.com/v1/hosts",
                    headers={"X-API-Key": key, "Accept": "application/json"},
                    params={"pageSize": 1},
                )
            if r.status_code == 200:
                return True, "OK"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, str(e)

    async def fetch_report_data(self, credential: IntegrationCredential) -> dict:
        """Hämtar UniFi-data redo för rapporten (körs i en bakgrundstråd)."""
        if not credential.api_key:
            raise ValueError("Ingen UniFi API-nyckel konfigurerad")
        api_key = decrypt(credential.api_key)
        return await asyncio.to_thread(self._fetch_sync, api_key)

    @staticmethod
    def _fetch_sync(api_key: str) -> dict:
        with UnifiClient(api_key) as client:
            sites = client.list_sites()
            devices = client.list_devices()
            isp_metrics = None
            if sites:
                try:
                    isp_metrics = client.query_isp_metrics(
                        site_id=sites[0].site_id,
                        host_id=sites[0].host_id,
                    )
                except Exception:
                    isp_metrics = None

        site = sites[0] if sites else None

        return {
            "integration": "unifi",
            "available": True,
            "site_summaries": [
                {
                    "name": s.name,
                    "total_devices": s.total_devices,
                    "offline_devices": s.offline_devices,
                    "pending_updates": s.pending_updates,
                    "wifi_clients": s.wifi_clients,
                    "wired_clients": s.wired_clients,
                    "gateway_model": s.gateway_model,
                    "ips_rules_count": s.ips_rules_count,
                    "wans": [
                        {
                            "name": w.name,
                            "uptime_percentage": w.uptime_percentage,
                            "isp_name": w.isp_name,
                            "isp_organization": w.isp_organization,
                            "external_ip": w.external_ip,
                            "has_issues": w.has_issues,
                            "issue_count": w.issue_count,
                        }
                        for w in s.wans
                    ],
                }
                for s in sites
            ],
            "device_summaries": [
                {
                    "name": d.name,
                    "model": d.model,
                    "product_line": d.product_line,
                    "is_online": d.is_online,
                    "firmware_version": d.firmware_version,
                    "firmware_status": d.firmware_status,
                    "needs_update": d.needs_update,
                    "adoption_time": d.adoption_time,
                }
                for d in devices
            ],
            "isp_metrics_raw": isp_metrics,
            "total_devices": site.total_devices if site else len(devices),
            "offline_devices": site.offline_devices if site else 0,
        }
