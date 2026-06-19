"""
Cloudfactory-integration.

Kommande funktionalitet:
- Aktiva licenser och tjänster per kund
- Förbrukningsdata
- Tjänststatus och eventuella avvikelser

Kontakta Cloudfactory för API-dokumentation och credentials.
"""

from dataclasses import dataclass


@dataclass
class CloudfactoryLicense:
    product_name: str
    quantity: int
    active: int
    expires_at: str | None


@dataclass
class CloudfactorySummary:
    licenses: list[CloudfactoryLicense]
    total_active: int


async def get_cloudfactory_summary(api_key: str) -> CloudfactorySummary:
    """
    Hämtar licensdata från Cloudfactory för en kund.
    Inte implementerat än — returnerar placeholder.
    """
    raise NotImplementedError("Cloudfactory-integration kommer i nästa version")
