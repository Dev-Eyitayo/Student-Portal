from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import jwt
import os
from passlib.context import CryptContext
from app.core.config import get_settings

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


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
