"""
UniFi Site Manager API-klient.

Varje kund har sin egen Fabric och därmed sin egen API-nyckel (verifierat
2026-06-18 - en delad/global nyckel ger tomt resultat även med 200 OK när
kontot inte har rätt scope mot en specifik Fabric).

Denna klient tar EN nyckel åt gången och representerar EN kunds data.
Appens högre lager (app/db, app/reports) ansvarar för att loopa över alla
kunders nycklar.

Dokumentation: https://developer.ui.com/site-manager/v1.0.0/gettingstarted
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ui.com/v1"
DEFAULT_TIMEOUT = 30.0


class UnifiApiError(Exception):
    """Höjs vid icke-2xx-svar från UniFi Site Manager API."""

    def __init__(self, status_code: int, message: str, trace_id: str | None = None):
        self.status_code = status_code
        self.trace_id = trace_id
        super().__init__(f"UniFi API-fel ({status_code}): {message} [traceId={trace_id}]")


class UnifiPartialSuccessError(Exception):
    """
    Höjs när UniFi svarar 'partialSuccess' - vissa siter kunde inte hämtas.
    Detta är INTE ett hårt fel - vi vill logga det och fortsätta med den
    data vi faktiskt fick.
    """

    def __init__(self, missing_site_ids: list[str]):
        self.missing_site_ids = missing_site_ids
        super().__init__(f"Partiell data - saknar siter: {missing_site_ids}")


@dataclass
class WanStatus:
    name: str
    external_ip: str | None
    isp_name: str | None
    isp_organization: str | None
    uptime_percentage: float | None
    has_issues: bool
    issue_count: int
    is_up: bool = True  # True om WAN har extern IP just nu (= uppkopplad)


@dataclass
class SiteSummary:
    """Sammanfattad vy av en site, redo att matas in i PDF-rapporten."""

    site_id: str
    host_id: str
    name: str
    timezone: str
    total_devices: int
    offline_devices: int
    pending_updates: int
    wifi_clients: int
    wired_clients: int
    gateway_model: str | None
    ips_rules_count: int | None
    wans: list[WanStatus]
    raw: dict[str, Any]


@dataclass
class DeviceSummary:
    """Sammanfattad vy av en enskild enhet (switch, AP, gateway, kamera)."""

    id: str
    name: str
    model: str | None
    shortname: str | None
    mac: str | None
    ip: str | None
    product_line: str  # "network" eller "protect"
    is_console: bool
    is_online: bool
    firmware_version: str | None
    firmware_status: str | None  # "upToDate" e.dyl. direkt från UniFi
    update_available_version: str | None  # None om ingen uppdatering väntar
    adoption_time: str | None
    raw: dict[str, Any]

    @property
    def needs_update(self) -> bool:
        return bool(self.update_available_version)


class UnifiClient:
    """
    Klient för en (1) kunds UniFi Fabric, autentiserad med en kund-specifik
    API-nyckel.

    Användning:
        client = UnifiClient(api_key="...")
        sites = client.list_sites()
        devices = client.list_devices(host_ids=[h.id for h in hosts])
        client.close()

    Eller som context manager:
        with UnifiClient(api_key="...") as client:
            sites = client.list_sites()
    """

    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: float = DEFAULT_TIMEOUT):
        if not api_key:
            raise ValueError("api_key får inte vara tom")
        self._client = httpx.Client(
            base_url=base_url,
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
        )

    def __enter__(self) -> "UnifiClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.RequestError as e:
            raise UnifiApiError(0, f"Nätverksfel: {e}") from e

        try:
            body = resp.json()
        except ValueError:
            body = {}

        if resp.status_code >= 400:
            raise UnifiApiError(
                resp.status_code,
                body.get("message", resp.text[:300]),
                trace_id=body.get("traceId"),
            )
        return body

    # ------------------------------------------------------------------
    # Hosts
    # ------------------------------------------------------------------

    def list_hosts(self) -> list[dict[str, Any]]:
        """
        Hämtar alla hosts (controllers/konsoler) som API-nyckeln har åtkomst
        till. För en kund-scopead nyckel blir detta normalt en (1) host -
        kundens gateway-konsol.
        """
        results: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": 100}
            if next_token:
                params["nextToken"] = next_token
            body = self._request("GET", "/hosts", params=params)
            results.extend(body.get("data", []))
            next_token = body.get("nextToken")
            if not next_token or not body.get("data"):
                break
        return results

    # ------------------------------------------------------------------
    # Sites
    # ------------------------------------------------------------------

    def list_sites(self) -> list[SiteSummary]:
        """
        Hämtar alla siter med statistik (enhetsantal, ISP-info, WAN-uptime).
        Detta är primärkällan för rapportens sammanfattningssektion.
        """
        results: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": 100}
            if next_token:
                params["nextToken"] = next_token
            body = self._request("GET", "/sites", params=params)
            results.extend(body.get("data", []))
            next_token = body.get("nextToken")
            if not next_token or not body.get("data"):
                break

        return [self._parse_site(s) for s in results]

    @staticmethod
    def _parse_site(raw: dict[str, Any]) -> SiteSummary:
        stats = raw.get("statistics", {})
        counts = stats.get("counts", {})
        gateway = stats.get("gateway", {})

        wans: list[WanStatus] = []
        for wan_name, wan_data in (stats.get("wans") or {}).items():
            # Använd ENDAST WAN-portens egna ISP-info — ingen fallback till
            # site-nivåns ISP, annars ärver en oanvänd WAN2 site-ISP:n och
            # ser ut att vara aktiv.
            wan_isp = wan_data.get("ispInfo") or {}
            issues = wan_data.get("wanIssues") or []
            external_ip = wan_data.get("externalIp")
            wans.append(
                WanStatus(
                    name=wan_name,
                    external_ip=external_ip,
                    isp_name=wan_isp.get("name"),
                    isp_organization=wan_isp.get("organization"),
                    uptime_percentage=wan_data.get("wanUptime"),
                    has_issues=len(issues) > 0,
                    issue_count=sum(i.get("count", 0) for i in issues),
                    is_up=bool(external_ip),
                )
            )

        return SiteSummary(
            site_id=raw.get("siteId", ""),
            host_id=raw.get("hostId", ""),
            name=(raw.get("meta") or {}).get("name", "Okänd site"),
            timezone=(raw.get("meta") or {}).get("timezone", ""),
            total_devices=counts.get("totalDevice", 0),
            offline_devices=counts.get("offlineDevice", 0),
            pending_updates=counts.get("pendingUpdateDevice", 0),
            wifi_clients=counts.get("wifiClient", 0),
            wired_clients=counts.get("wiredClient", 0),
            gateway_model=gateway.get("shortname"),
            ips_rules_count=(gateway.get("ipsSignature") or {}).get("rulesCount"),
            wans=wans,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def list_devices(self, host_ids: list[str] | None = None) -> list[DeviceSummary]:
        """Flat lista av alla enheter — bakåtkompatibel."""
        result = []
        for group in self.list_devices_grouped(host_ids):
            result.extend(group["devices"])
        return result

    def list_devices_grouped(
        self, host_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """
        Returnerar enheter grupperade per host: [{host_id, host_name, devices}].
        Bevarar den naturliga grupperingen från UniFi-API:t (en host = en Fabric).
        """
        params: list[tuple[str, str]] = [("pageSize", "100")]
        if host_ids:
            params.extend(("hostIds[]", hid) for hid in host_ids)

        body = self._request("GET", "/devices", params=params)
        result: list[dict[str, Any]] = []
        for host_entry in body.get("data", []):
            result.append({
                "host_id": host_entry.get("hostId", ""),
                "host_name": host_entry.get("hostName", ""),
                "devices": [
                    self._parse_device(d) for d in host_entry.get("devices", [])
                ],
            })
        return result

    @staticmethod
    def _parse_device(raw: dict[str, Any]) -> DeviceSummary:
        # UniFi sätter updateAvailable till en tom sträng när ingen
        # uppdatering väntar, och till målversionen när en finns.
        update_available = raw.get("updateAvailable") or None

        return DeviceSummary(
            id=raw.get("id", raw.get("mac", "okänd")),
            name=raw.get("name", "Okänd enhet"),
            model=raw.get("model"),
            shortname=raw.get("shortname"),
            mac=raw.get("mac"),
            ip=raw.get("ip"),
            product_line=raw.get("productLine", "network"),
            is_console=bool(raw.get("isConsole", False)),
            is_online=raw.get("status", "").lower() == "online",
            firmware_version=raw.get("version"),
            firmware_status=raw.get("firmwareStatus"),
            update_available_version=update_available,
            adoption_time=raw.get("adoptionTime"),
            raw=raw,
        )

    # ------------------------------------------------------------------
    # ISP metrics (historik - uptime, latens, packet loss)
    # ------------------------------------------------------------------

    def query_isp_metrics(
        self,
        site_id: str,
        host_id: str,
        begin: datetime | None = None,
        end: datetime | None = None,
        granularity: str = "1h",
    ) -> dict[str, Any]:
        """
        Hämtar historiska ISP-mått (uptime, latens, packet loss) för en site
        under en tidsperiod. Standard: senaste 30 dagarna med 1h-upplösning,
        lämpligt för en månadsrapport.

        granularity: "5m" (kräver <=24h fönster) eller "1h"/"1d" (längre fönster).
        """
        if end is None:
            end = datetime.now(timezone.utc)
        if begin is None:
            begin = end - timedelta(days=30)

        payload = {
            "sites": [
                {
                    "hostId": host_id,
                    "siteId": site_id,
                    "beginTimestamp": _iso(begin),
                    "endTimestamp": _iso(end),
                }
            ]
        }
        body = self._request(
            "POST",
            f"/isp-metrics/{granularity}/query",
            json=payload,
        )

        # Hantera partialSuccess explicit istället för att tysta svälja det -
        # ett ofullständigt svar ska synas i loggarna, inte bara i rapporten.
        if body.get("status") == "partialSuccess":
            missing = body.get("missingSiteIds", [])
            logger.warning(
                "Partiell ISP-metrics-data för site %s (host %s): saknar %s",
                site_id,
                host_id,
                missing,
            )

        return body


def _iso(dt: datetime) -> str:
    """Formaterar en datetime som ISO 8601 med Z-suffix, vilket UniFi API kräver."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
