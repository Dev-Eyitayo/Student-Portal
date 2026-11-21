from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import ColumnElement, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_authenticated_user, get_tenant_db
from app.core.exceptions import InsufficientPermissionException
from app.modules.identity.models import (
    Permission,
    RoleDefaultPermission,
    SchoolRolePermission,
    User,
    UserPermissionGrant,
)


async def role_has_permission(
    db: AsyncSession,
    school_id: UUID,
    role: str,
    permission_code: str,
) -> bool:
    """Checks school-specific role overrides first, falling back to global role defaults."""
    override_exists = (
        await db.execute(
            select(SchoolRolePermission.id)
            .where(
                SchoolRolePermission.school_id == school_id,
                SchoolRolePermission.role == role,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if override_exists is not None:
        stmt_school_role = (
            select(SchoolRolePermission.id)
            .join(Permission, Permission.id == SchoolRolePermission.permission_id)
            .where(
                SchoolRolePermission.school_id == school_id,
                SchoolRolePermission.role == role,
                SchoolRolePermission.is_enabled.is_(True),
                Permission.code == permission_code,
            )
        )
        return (await db.execute(stmt_school_role)).scalar_one_or_none() is not None

    stmt_role = (
        select(Permission.id)
        .join(RoleDefaultPermission, RoleDefaultPermission.permission_id == Permission.id)
        .where(RoleDefaultPermission.role == role, Permission.code == permission_code)
    )
    return (await db.execute(stmt_role)).scalar_one_or_none() is not None


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

    role_has_perm = await role_has_permission(db, user.school_id, user.role, permission_code)
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


async def require_scoped_permission(
    db: AsyncSession,
    user: User,
    permission_code: str,
    *,
    department_id: UUID | None = None,
    academic_level_id: UUID | None = None,
    class_group_id: UUID | None = None,
) -> None:
    if await user_has_permission(db, user, permission_code):
        return
    if await user_has_permission(
        db,
        user,
        permission_code,
        department_id=department_id,
        academic_level_id=academic_level_id,
        class_group_id=class_group_id,
    ):
        return
    raise InsufficientPermissionException(permission_code=permission_code)


@dataclass
class PermissionScope:
    """The effective scope a user holds for one permission code, for use in
    list queries that can't check a single resource's scope the way
    require_scoped_permission does -- they need to filter many rows instead.
    """

    unscoped: bool
    department_ids: set[UUID] = field(default_factory=set)
    academic_level_ids: set[UUID] = field(default_factory=set)
    class_group_ids: set[UUID] = field(default_factory=set)

    @property
    def has_any_access(self) -> bool:
        return self.unscoped or bool(
            self.department_ids or self.academic_level_ids or self.class_group_ids
        )


async def get_permission_scope(
    db: AsyncSession, user: User, permission_code: str
) -> PermissionScope:
    if user.role in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return PermissionScope(unscoped=True)

    if await role_has_permission(db, user.school_id, user.role, permission_code):
        return PermissionScope(unscoped=True)

    stmt_grants = (
        select(UserPermissionGrant)
        .join(Permission, Permission.id == UserPermissionGrant.permission_id)
        .where(
            UserPermissionGrant.user_id == user.id,
            UserPermissionGrant.school_id == user.school_id,
            UserPermissionGrant.deleted_at.is_(None),
            Permission.code == permission_code,
        )
    )
    grants = (await db.execute(stmt_grants)).scalars().all()

    scope = PermissionScope(unscoped=False)
    for grant in grants:
        if grant.scope_type == "SCHOOL_WIDE":
            scope.unscoped = True
        elif grant.scope_type == "DEPARTMENT" and grant.department_id is not None:
            scope.department_ids.add(grant.department_id)
        elif grant.scope_type == "ACADEMIC_LEVEL" and grant.academic_level_id is not None:
            scope.academic_level_ids.add(grant.academic_level_id)
        elif grant.scope_type == "CLASS_GROUP" and grant.class_group_id is not None:
            scope.class_group_ids.add(grant.class_group_id)
    return scope


def build_scope_filter(
    scope: PermissionScope,
    *,
    department_column: ColumnElement | None = None,
    academic_level_column: ColumnElement | None = None,
    class_group_column: ColumnElement | None = None,
) -> ColumnElement[bool]:
    """Builds the WHERE clause for a list query restricted to a non-unscoped
    PermissionScope. Pass only the columns that dimension applies to for this
    resource (e.g. a SubjectOffering has no class group, so omit that column).
    Call only when scope.unscoped is False -- there is nothing to filter
    otherwise.
    """
    conditions: list[ColumnElement[bool]] = []
    if department_column is not None and scope.department_ids:
        conditions.append(department_column.in_(scope.department_ids))
    if academic_level_column is not None and scope.academic_level_ids:
        conditions.append(academic_level_column.in_(scope.academic_level_ids))
    if class_group_column is not None and scope.class_group_ids:
        conditions.append(class_group_column.in_(scope.class_group_ids))
    if not conditions:
        return false()
    return or_(*conditions)


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
