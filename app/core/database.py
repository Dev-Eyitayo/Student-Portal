from collections.abc import AsyncGenerator
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

_prepared_statement_cache_size = settings.DB_PREPARED_STATEMENT_CACHE_SIZE
if _prepared_statement_cache_size is None:
    # PgBouncer in transaction mode (Neon's -pooler endpoint) is safest with
    # asyncpg's statement cache disabled. Direct connections keep the cache.
    _prepared_statement_cache_size = 0 if "-pooler" in (settings.DATABASE_URL or "") else 100

_connect_args: dict = {
    "prepared_statement_cache_size": _prepared_statement_cache_size,
}
if settings.DB_SSL_MODE:
    _connect_args["ssl"] = settings.DB_SSL_MODE

_engine_options: dict = {
    "echo": settings.DEBUG,
    "future": True,
    "pool_pre_ping": True,
    "connect_args": _connect_args,
}

# Tests run each coroutine on its own event loop; pooled asyncpg connections
# cannot be reused across loops, so disable pooling in the test environment.
if settings.ENVIRONMENT in ("testing", "test"):
    _engine_options["poolclass"] = NullPool
else:
    _engine_options["pool_size"] = settings.DB_POOL_SIZE
    _engine_options["max_overflow"] = settings.DB_MAX_OVERFLOW
    _engine_options["pool_recycle"] = settings.DB_POOL_RECYCLE_SECONDS

async_engine = create_async_engine(settings.DATABASE_URL, **_engine_options)

# Tenant context is carried per-request through context variables so the
# transaction-begin hook below can apply transaction-local RLS settings to
# every transaction on the session, including those started after a commit().
_tenant_school_id: ContextVar[str] = ContextVar("tenant_school_id", default="")
_tenant_is_super_admin: ContextVar[bool] = ContextVar("tenant_is_super_admin", default=False)


@event.listens_for(async_engine.sync_engine, "begin")
def _apply_tenant_context(conn) -> None:
    """Apply the current tenant context at the start of every transaction."""
    conn.execute(
        text(
            "SELECT set_config('app.current_school_id', :school_id, true), "
            "set_config('app.is_super_admin', :is_super_admin, true)"
        ),
        {
            "school_id": _tenant_school_id.get(),
            "is_super_admin": "true" if _tenant_is_super_admin.get() else "false",
        },
    )


def set_tenant_scope(
    school_id: object | None, is_super_admin: bool = False
) -> tuple[object, object]:
    """Set the tenant scope for subsequent transactions in the current context."""
    return (
        _tenant_school_id.set(str(school_id) if school_id else ""),
        _tenant_is_super_admin.set(bool(is_super_admin)),
    )


def reset_tenant_scope(tokens: tuple[object, object]) -> None:
    school_token, super_admin_token = tokens
    _tenant_school_id.reset(school_token)
    _tenant_is_super_admin.reset(super_admin_token)


async_session_factory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_raw_db() -> AsyncGenerator[AsyncSession, None]:
    """Yields a database session without tenant Row-Level Security set."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
