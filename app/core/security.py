import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.redis import get_redis
from app.modules.common.models import generate_uuid7

settings = get_settings()
logger = logging.getLogger(__name__)

# In test mode, reduce Argon2 cost parameters to ensure blazing-fast test execution (< 5ms per hash)
is_testing = settings.ENVIRONMENT in ("testing", "test") or os.getenv("TESTING") == "1"

if is_testing:
    pwd_context = CryptContext(
        schemes=["argon2"],
        deprecated="auto",
        argon2__time_cost=1,
        argon2__memory_cost=512,
        argon2__parallelism=1,
    )
else:
    # OWASP-recommended interactive Argon2id parameters (19 MiB, t=2, p=1)
    pwd_context = CryptContext(
        schemes=["argon2"],
        deprecated="auto",
        argon2__time_cost=2,
        argon2__memory_cost=19456,
        argon2__parallelism=1,
    )

# In-memory revocation cache for testing or when Redis is offline
_in_memory_revoked_tokens: set[str] = set()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    data: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
    *,
    subject: str | None = None,
    school_id: Any | None = None,
    role: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Creates an access token featuring a unique time-ordered 'jti' (JWT ID).
    The 'jti' allows immediate individual token revocation via Redis blacklist.
    Supports dictionary payload or explicit subject/claims parameters.
    """
    to_encode: dict[str, Any] = {}
    if data:
        to_encode.update(data)
    if subject:
        to_encode["sub"] = str(subject)
    if school_id:
        to_encode["school_id"] = str(school_id)
    if role:
        to_encode["role"] = role
    if extra_claims:
        to_encode.update(extra_claims)

    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    jti = str(generate_uuid7())
    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "jti": jti,
        }
    )
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(
    subject: str,
    school_id: Any | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Creates a long-lived refresh token for token rotation.
    """
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode = {
        "sub": str(subject),
        "school_id": str(school_id) if school_id else None,
        "type": "refresh",
        "jti": str(generate_uuid7()),
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


decode_token = decode_access_token


async def revoke_access_token(jti: str, expires_in_seconds: int = 900) -> None:
    """
    Instantly blacklists a specific access token by its jti.
    Key will automatically expire in Redis after token's remaining lifespan.
    """
    _in_memory_revoked_tokens.add(jti)
    if is_testing:
        return
    try:
        client = get_redis()
        await client.set(f"token:revoked:{jti}", "1", ex=max(1, expires_in_seconds))
    except Exception:
        # Falls back to the in-memory store in local dev or during Redis outages.
        logger.warning(
            "Could not persist token revocation in Redis; using in-memory fallback",
            exc_info=True,
        )


async def is_token_revoked(jti: str) -> bool:
    """Checks whether a token jti has been blacklisted."""
    if jti in _in_memory_revoked_tokens:
        return True
    if is_testing:
        return False
    try:
        client = get_redis()
        return bool(await client.exists(f"token:revoked:{jti}"))
    except Exception:
        logger.debug("Redis unavailable while checking token revocation", exc_info=True)
        return False
