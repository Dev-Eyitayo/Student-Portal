from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class RLSEnforcementError(RuntimeError):
    """Raised when the application would run with Row-Level Security bypassed."""


async def verify_rls_enforcement(session: AsyncSession) -> None:
    """Fail fast if the connected role can bypass PostgreSQL RLS.

    Superusers and roles with BYPASSRLS ignore every row-security policy, even
    when ``FORCE ROW LEVEL SECURITY`` is set. Production must therefore connect
    with a dedicated non-superuser, non-BYPASSRLS role.
    """
    result = await session.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    )
    row = result.one_or_none()
    if row is None:
        raise RLSEnforcementError(
            "Unable to verify the database role for Row-Level Security. "
            "Check that the application can read pg_roles."
        )

    is_superuser, bypass_rls = row
    if is_superuser or bypass_rls:
        raise RLSEnforcementError(
            "Refusing to start: the application database role can bypass "
            "PostgreSQL Row-Level Security. Connect with a dedicated "
            "NOSUPERUSER, NOBYPASSRLS role (for example, a SQL-created role on "
            "Neon) and reserve the admin role for Alembic migrations."
        )
