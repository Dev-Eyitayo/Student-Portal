from datetime import datetime, timezone
from typing import AsyncGenerator, Optional
from uuid import UUID
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import async_session_factory
from app.core.exceptions import DomainException
from app.core.security import decode_access_token, is_token_revoked
from app.modules.identity.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/identity/login")


async def get_current_authenticated_user(
    token: str = Depends(oauth2_scheme)
) -> User:
    try:
        payload = decode_access_token(token)
        user_id_str: str = payload.get("sub")
        jti: Optional[str] = payload.get("jti")
        iat_ts: Optional[float] = payload.get("iat")

        if not user_id_str or not jti:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload."
            )
        user_id = UUID(user_id_str)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials."
        )

    # 1. Fast Redis Blacklist Check (Instant revocation by jti)
    if await is_token_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked."
        )

    async with async_session_factory() as session:
        stmt = select(User).where(User.id == user_id, User.is_active.is_(True))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or account is deactivated."
            )

        # 2. Global Invalidation Check (e.g. password reset or admin deactivation)
        if iat_ts is not None and user.token_valid_after:
            token_issued_at = datetime.fromtimestamp(iat_ts, tz=timezone.utc)
            if token_issued_at < user.token_valid_after:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session has been revoked. Please log in again."
                )

        return user


async def get_tenant_db(
    current_user: User = Depends(get_current_authenticated_user),
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yields an AsyncSession with PostgreSQL Row-Level Security (RLS)
    scoped strictly to the authenticated user's school_id.
    """
    if not current_user.school_id and current_user.role != "SUPER_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with an active school tenant."
        )

    async with async_session_factory() as session:
        if current_user.school_id:
            await session.execute(
                text("SET LOCAL app.current_school_id = :school_id"),
                {"school_id": str(current_user.school_id)}
            )
        if current_user.role == "SUPER_ADMIN":
            await session.execute(text("SET LOCAL app.is_super_admin = 'true'"))

        try:
            yield session
        finally:
            await session.close()
