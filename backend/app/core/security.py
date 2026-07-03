"""
Säkerhetsfunktioner: JWT-tokens och symmetrisk kryptering av API-nycklar.
API-nycklar (UniFi, Graph, Acronis, Cloudfactory) krypteras med Fernet AES-128
via cryptography-biblioteket innan de lagras i databasen.
"""

from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from fastapi import HTTPException, status
from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_BCRYPT_MAX_BYTES = 72


def _fernet() -> Fernet:
    """
    Stöder två nyckelformat:
    - Ny stil: en giltig Fernet-nyckel (44 tecken URL-safe base64, t.ex. från Fernet.generate_key()).
      Generera med: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    - Gammal stil (bakåtkompatibilitet): valfri sträng — konverteras till 32-byte nyckel
      på samma sätt som originalkoden för att befintliga krypterade värden ska kunna läsas.
    """
    import base64 as _b64
    key = settings.ENCRYPTION_KEY
    if not key:
        raise RuntimeError("ENCRYPTION_KEY saknas i miljövariablerna")
    key_bytes = key.encode()
    # Fernet-nycklar är alltid 44 tecken (32 bytes base64url-kodade)
    if len(key_bytes) == 44:
        try:
            return Fernet(key_bytes)
        except Exception:
            pass
    # Bakåtkompatibel härledning för befintliga installationer
    derived = _b64.urlsafe_b64encode(key_bytes[:32].ljust(32, b"0"))
    return Fernet(derived)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def _validate_password_length(password: str) -> None:
    if len(password.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Lösenordet är för långt — max {_BCRYPT_MAX_BYTES} bytes (UTF-8).",
        )


def hash_password(password: str) -> str:
    _validate_password_length(password)
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    _validate_password_length(plain)
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return jwt.encode(
        {"sub": subject, "exp": expire},
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def decode_token(token: str) -> str:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    return payload["sub"]


def create_reset_token(email: str, minutes: int = 60) -> str:
    """Kortlivad token för lösenordsåterställning (separat 'type' så den ej kan användas som inloggning)."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode(
        {"sub": email, "type": "pwreset", "exp": expire},
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def decode_reset_token(token: str) -> str:
    """Returnerar e-post om token är en giltig, ej utgången återställningstoken."""
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    if payload.get("type") != "pwreset":
        raise ValueError("Fel token-typ")
    return payload["sub"]
