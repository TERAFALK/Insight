"""
Acronis Backup-integration.

Kommande funktionalitet:
- Lista alla backup-jobb per kund
- Status: lyckade, misslyckade, varningar
- Senaste körningstid och nästa schemalagda körning
- Skyddade enheter och datamängd

API-dokumentation: https://developer.acronis.com/doc/account-management/v2/
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class AcronisJobStatus:
    device_name: str
    last_run: datetime | None
    status: str  # "ok" | "warning" | "error" | "never"
    protected_gb: float | None


@dataclass
class AcronisSummary:
    jobs: list[AcronisJobStatus]
    ok_count: int
    warning_count: int
    error_count: int


async def get_acronis_summary(api_key: str) -> AcronisSummary:
    """
    Hämtar Acronis backup-status för en kund.
    Inte implementerat än — returnerar placeholder.
    """
    raise NotImplementedError("Acronis-integration kommer i nästa version")
