from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import (
    async_session_factory,
    reset_tenant_scope,
    set_tenant_scope,
)
from app.core.security import decode_access_token, is_token_revoked
from app.modules.audit.services import AuditContext
from app.modules.identity.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/identity/login")


async def _get_request_db() -> AsyncGenerator[AsyncSession, None]:
    """Single request-scoped AsyncSession shared between auth resolution and route handlers."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_current_authenticated_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(_get_request_db),
) -> User:
    try:
        payload = decode_access_token(token)
        user_id_str: str = payload.get("sub")
        jti: str | None = payload.get("jti")
        iat_ts: float | None = payload.get("iat")

        if not user_id_str or not jti:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Your sign-in token is invalid. Please sign in again.",
            )
        user_id = UUID(user_id_str)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session is invalid or has expired. Please sign in again to continue.",
        ) from None

    # 1. Fast Redis Blacklist Check (Instant revocation by jti)
    if await is_token_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session has been signed out. Please sign in again to continue.",
        )

    stmt = (
        select(User)
        .options(selectinload(User.staff_profile), selectinload(User.school))
        .where(User.id == user_id, User.is_active.is_(True))
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account could not be found or has been deactivated. Please contact your school administrator.",
        )

    # 2. Global Invalidation Check (e.g. password reset or admin deactivation)
    if iat_ts is not None and user.token_valid_after:
        token_issued_at = datetime.fromtimestamp(iat_ts, tz=UTC)
        if token_issued_at < user.token_valid_after.replace(microsecond=0):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="You have been signed out because your account details or password were updated. Please sign in again.",
            )

    return user


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yields a standard database session without RLS scoping (for public or unauthenticated endpoints)."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_tenant_db(
    current_user: User = Depends(get_current_authenticated_user),
    session: AsyncSession = Depends(_get_request_db),
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yields an AsyncSession with PostgreSQL Row-Level Security (RLS)
    scoped strictly to the authenticated user's school_id.
    """
    if not current_user.school_id and current_user.role != "SUPER_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not linked to an active school. Please reach out to your school administrator.",
        )

    tokens = set_tenant_scope(current_user.school_id, current_user.role == "SUPER_ADMIN")
    try:
        # Apply RLS settings to the already-open transaction from auth lookup
        await session.execute(
            text(
                "SELECT set_config('app.current_school_id', :school_id, true), "
                "set_config('app.is_super_admin', :is_super_admin, true)"
            ),
            {
                "school_id": str(current_user.school_id) if current_user.school_id else "",
                "is_super_admin": "true" if current_user.role == "SUPER_ADMIN" else "false",
            },
        )
        yield session
    finally:
        reset_tenant_scope(tokens)


def require_school_id(current_user: User) -> UUID:
    """Raises 403 if the user has no school_id (e.g. a SUPER_ADMIN acting outside tenant scope)."""
    if current_user.school_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not linked to an active school. Please contact your school administrator.",
        )
    return current_user.school_id


def build_audit_context(request: Request, current_user: User) -> AuditContext:
    client_ip = request.client.host if request.client else "0.0.0.0"
    return AuditContext(
        school_id=require_school_id(current_user),
        actor_id=current_user.id,
        actor_ip=client_ip,
    )
