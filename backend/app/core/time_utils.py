from datetime import datetime
from zoneinfo import ZoneInfo

TZ_STOCKHOLM = ZoneInfo("Europe/Stockholm")


def now_stockholm() -> datetime:
    """Aktuell tid i Europe/Stockholm som naiv datetime (för TIMESTAMP WITHOUT TIME ZONE i DB)."""
    return datetime.now(TZ_STOCKHOLM).replace(tzinfo=None)
