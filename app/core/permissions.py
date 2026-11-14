from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_authenticated_user, get_tenant_db
from app.core.exceptions import InsufficientPermissionException
from app.modules.identity.models import (
    Permission,
    RoleDefaultPermission,
    User,
    UserPermissionGrant,
)


def require_permission(
    permission_code: str, scope_extractor: Callable[[Request], dict[str, UUID | None]] | None = None
) -> Callable:
    """
    FastAPI dependency factory enforcing the Hybrid Permission Model.
    Access is granted if:
      1. User is SUPER_ADMIN or SCHOOL_ADMIN, OR
      2. User's role has the default permission (and no specific scope is required), OR
      3. User has a direct UserPermissionGrant matching the required organizational scope
         (SCHOOL_WIDE, or matching department, level, or class group).
    """

    async def permission_checker(
        request: Request,
        current_user: User = Depends(get_current_authenticated_user),
        db: AsyncSession = Depends(get_tenant_db),
    ) -> bool:
        # 1. School Admin and Platform Super Admin have full access
        if current_user.role in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
            return True

        # 2. Check Role-level default permissions
        stmt_role = (
            select(Permission)
            .join(RoleDefaultPermission, RoleDefaultPermission.permission_id == Permission.id)
            .where(
                RoleDefaultPermission.role == current_user.role, Permission.code == permission_code
            )
        )
        role_has_perm = (await db.execute(stmt_role)).scalar_one_or_none()
        if role_has_perm and not scope_extractor:
            return True

        # 3. Extract target scope context if applicable
        target_context = scope_extractor(request) if scope_extractor else {}
        target_dept_id = target_context.get("department_id")
        target_level_id = target_context.get("academic_level_id")
        target_class_group_id = target_context.get("class_group_id")

        # 4. Check Direct Scoped User Grants
        stmt_grant = (
            select(UserPermissionGrant)
            .join(Permission, Permission.id == UserPermissionGrant.permission_id)
            .where(
                UserPermissionGrant.user_id == current_user.id,
                UserPermissionGrant.school_id == current_user.school_id,
                Permission.code == permission_code,
                or_(
                    UserPermissionGrant.scope_type == "SCHOOL_WIDE",
                    UserPermissionGrant.department_id == target_dept_id,
                    UserPermissionGrant.academic_level_id == target_level_id,
                    UserPermissionGrant.class_group_id == target_class_group_id,
                ),
            )
        )
        matching_grant = (await db.execute(stmt_grant)).scalar_one_or_none()
        if matching_grant:
            return True

        raise InsufficientPermissionException(permission_code=permission_code)

    return permission_checker
