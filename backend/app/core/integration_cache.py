"""
In-memory cache för integration-data per kund.

Mönster: stale-while-revalidate
- Första anropet hämtar live och sparar i cache.
- Efterföljande anrop returnerar cache direkt och triggar
  en bakgrundsuppdatering om datan är äldre än CACHE_TTL_SECONDS.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300  # 5 minuter

CacheKey = tuple[str, str]  # (customer_id, integration_type)


@dataclass
class CacheEntry:
    data: Any
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    refreshing: bool = False

    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.fetched_at).total_seconds()

    def is_stale(self) -> bool:
        return self.age_seconds() > CACHE_TTL_SECONDS


_cache: dict[CacheKey, CacheEntry] = {}


def get_cached(customer_id: str, integration_type: str) -> CacheEntry | None:
    return _cache.get((customer_id, integration_type))


def set_cached(customer_id: str, integration_type: str, data: Any) -> None:
    key = (customer_id, integration_type)
    entry = _cache.get(key)
    if entry:
        entry.data = data
        entry.fetched_at = datetime.now(timezone.utc)
        entry.refreshing = False
    else:
        _cache[key] = CacheEntry(data=data)


def mark_refreshing(customer_id: str, integration_type: str) -> None:
    entry = _cache.get((customer_id, integration_type))
    if entry:
        entry.refreshing = True


async def refresh_in_background(
    customer_id: str,
    integration_type: str,
    fetch_fn,
) -> None:
    """Kör fetch_fn i bakgrunden och uppdaterar cachen när klar."""
    mark_refreshing(customer_id, integration_type)
    try:
        data = await fetch_fn()
        set_cached(customer_id, integration_type, data)
        logger.info("Cache uppdaterad: %s/%s", customer_id, integration_type)
    except Exception as e:
        entry = get_cached(customer_id, integration_type)
        if entry:
            entry.refreshing = False
        logger.warning("Bakgrundsuppdatering misslyckades för %s/%s: %s", customer_id, integration_type, e)
