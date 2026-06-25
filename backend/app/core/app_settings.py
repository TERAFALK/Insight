"""
Runtime app-inställningar — sparas i DB, redigerbara från UI.
Laddas vid uppstart med .env som fallback. Uppdateras live utan omstart.
"""

import logging

logger = logging.getLogger(__name__)

# db_key → (env_key, default)
_KEYS: dict[str, tuple[str, str]] = {
    "graph_tenant_id":      ("GRAPH_TENANT_ID",      ""),
    "graph_client_id":      ("GRAPH_CLIENT_ID",       ""),
    "graph_client_secret":  ("GRAPH_CLIENT_SECRET",   ""),
    "graph_sender":         ("GRAPH_SENDER",          "noreply@terafalk.com"),
    "support_inbox":        ("SUPPORT_INBOX",         "support@terafalk.com"),
    "ms_app_client_id":     ("MS_APP_CLIENT_ID",      ""),
    "ms_app_client_secret": ("MS_APP_CLIENT_SECRET",  ""),
    "ms_app_redirect_uri":  ("MS_APP_REDIRECT_URI",   ""),
}

_SECRET_KEYS = {"graph_client_secret", "ms_app_client_secret"}

_store: dict[str, str] = {}


def get(key: str) -> str:
    return _store.get(key, "")


def all_settings(mask_secrets: bool = True) -> dict[str, str]:
    return {
        k: ("••••••••" if mask_secrets and k in _SECRET_KEYS and v else v)
        for k, v in _store.items()
    }


async def load_from_db() -> None:
    """Körs vid uppstart. Läser från DB, faller tillbaka på .env-värden och migrerar dem till DB."""
    from app.core.config import settings as env
    from app.db.database import AsyncSessionLocal
    from app.db.models import SystemSetting

    async with AsyncSessionLocal() as db:
        migrated = False
        for db_key, (env_key, default) in _KEYS.items():
            row = await db.get(SystemSetting, db_key)
            if row:
                _store[db_key] = row.value
            else:
                env_val = getattr(env, env_key, default) or default
                _store[db_key] = env_val
                if env_val:
                    db.add(SystemSetting(key=db_key, value=env_val))
                    migrated = True
        if migrated:
            await db.commit()
            logger.info("App-inställningar migrerade från .env till DB")


async def update(key: str, value: str) -> None:
    """Uppdaterar ett värde i minnet och DB omedelbart."""
    if key not in _KEYS:
        raise ValueError(f"Okänd inställning: {key}")
    _store[key] = value
    from app.db.database import AsyncSessionLocal
    from app.db.models import SystemSetting

    async with AsyncSessionLocal() as db:
        row = await db.get(SystemSetting, key)
        if row:
            row.value = value
        else:
            db.add(SystemSetting(key=key, value=value))
        await db.commit()
