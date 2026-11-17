from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_authenticated_user, get_tenant_db
from app.core.exceptions import InsufficientPermissionException
from app.modules.identity.models import (
    Permission,
    RoleDefaultPermission,
    User,
    UserPermissionGrant,
)


async def user_has_permission(
    db: AsyncSession,
    user: User,
    permission_code: str,
    *,
    department_id: UUID | None = None,
    academic_level_id: UUID | None = None,
    class_group_id: UUID | None = None,
) -> bool:
    """
    Core Hybrid Permission Model check, usable both as the require_permission
    FastAPI dependency and directly from service code (e.g. to check a scoped
    delegation against a resource resolved from a request body rather than a
    path/query parameter). Access is granted if:
      1. User is SUPER_ADMIN or SCHOOL_ADMIN, OR
      2. User's role has the default permission (and no specific scope is required), OR
      3. User has a direct UserPermissionGrant matching the required organizational scope
         (SCHOOL_WIDE, or matching department, level, or class group).
    """
    if user.role in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return True

    scope_requested = (
        department_id is not None or academic_level_id is not None or class_group_id is not None
    )

    stmt_role = (
        select(Permission)
        .join(RoleDefaultPermission, RoleDefaultPermission.permission_id == Permission.id)
        .where(RoleDefaultPermission.role == user.role, Permission.code == permission_code)
    )
    role_has_perm = (await db.execute(stmt_role)).scalar_one_or_none()
    if role_has_perm and not scope_requested:
        return True

    # Each scope dimension is only compared when the grant's own scope_type
    # matches it. Without this guard, SQLAlchemy compiles `Column == None`
    # as `IS NULL`, so a DEPARTMENT-scoped grant (whose academic_level_id/
    # class_group_id are NULL) would wrongly satisfy an ACADEMIC_LEVEL or
    # CLASS_GROUP check whenever the caller doesn't supply that dimension
    # (the normal case) -- granting access the delegation was never scoped to.
    stmt_grant = (
        select(UserPermissionGrant)
        .join(Permission, Permission.id == UserPermissionGrant.permission_id)
        .where(
            UserPermissionGrant.user_id == user.id,
            UserPermissionGrant.school_id == user.school_id,
            UserPermissionGrant.deleted_at.is_(None),
            Permission.code == permission_code,
            or_(
                UserPermissionGrant.scope_type == "SCHOOL_WIDE",
                and_(
                    UserPermissionGrant.scope_type == "DEPARTMENT",
                    UserPermissionGrant.department_id == department_id,
                ),
                and_(
                    UserPermissionGrant.scope_type == "ACADEMIC_LEVEL",
                    UserPermissionGrant.academic_level_id == academic_level_id,
                ),
                and_(
                    UserPermissionGrant.scope_type == "CLASS_GROUP",
                    UserPermissionGrant.class_group_id == class_group_id,
                ),
            ),
        )
    )
    matching_grant = (await db.execute(stmt_grant)).scalar_one_or_none()
    return matching_grant is not None


def require_permission(
    permission_code: str, scope_extractor: Callable[[Request], dict[str, UUID | None]] | None = None
) -> Callable:
    """FastAPI dependency factory enforcing the Hybrid Permission Model (see user_has_permission)."""

    async def permission_checker(
        request: Request,
        current_user: User = Depends(get_current_authenticated_user),
        db: AsyncSession = Depends(get_tenant_db),
    ) -> bool:
        target_context = scope_extractor(request) if scope_extractor else {}
        allowed = await user_has_permission(
            db,
            current_user,
            permission_code,
            department_id=target_context.get("department_id"),
            academic_level_id=target_context.get("academic_level_id"),
            class_group_id=target_context.get("class_group_id"),
        )
        if not allowed:
            raise InsufficientPermissionException(permission_code=permission_code)
        return True

    return permission_checker
