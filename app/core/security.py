from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import os
import jwt
from passlib.context import CryptContext
from app.core.config import get_settings
from app.modules.common.models import generate_uuid7

settings = get_settings()

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
    pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# In-memory revocation cache for testing or when Redis is offline
_in_memory_revoked_tokens: set[str] = set()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Creates an access token featuring a unique time-ordered 'jti' (JWT ID).
    The 'jti' allows immediate individual token revocation via Redis blacklist.
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    jti = str(generate_uuid7())
    to_encode.update({
        "exp": expire,
        "iat": now,
        "jti": jti,
    })
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


async def revoke_access_token(jti: str, expires_in_seconds: int = 900) -> None:
    """
    Instantly blacklists a specific access token by its jti.
    Key will automatically expire in Redis after token's remaining lifespan.
    """
    _in_memory_revoked_tokens.add(jti)
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await client.set(f"token:revoked:{jti}", "1", ex=max(1, expires_in_seconds))
        await client.aclose()
    except Exception:
        # Falls back to in-memory store in local dev/tests
        pass


async def is_token_revoked(jti: str) -> bool:
    """Checks whether a token jti has been blacklisted."""
    if jti in _in_memory_revoked_tokens:
        return True
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        is_revoked = await client.exists(f"token:revoked:{jti}")
        await client.aclose()
        return bool(is_revoked)
    except Exception:
        return False
