from typing import Dict, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.identity.models import Permission, RoleDefaultPermission


SYSTEM_PERMISSIONS = [
    # Assessment
    {"code": "scores:enter", "name": "Enter Raw Scores", "category": "ASSESSMENT", "description": "Enter continuous assessment and examination scores for assigned subjects."},
    {"code": "scores:modify", "name": "Modify Scores", "category": "ASSESSMENT", "description": "Modify unsubmitted score records."},
    {"code": "scores:submit", "name": "Submit Score Sheet", "category": "ASSESSMENT", "description": "Submit a completed subject score sheet for review and approval."},
    {"code": "scores:approve", "name": "Approve Scores", "category": "ASSESSMENT", "description": "Approve score sheets across assigned department or academic level."},
    {"code": "scores:unlock", "name": "Unlock Score Sheet", "category": "ASSESSMENT", "description": "Reopen a submitted or approved score sheet for corrections."},
    # Student
    {"code": "students:register", "name": "Register Student", "category": "STUDENT", "description": "Register individual student records and demographics."},
    {"code": "students:bulk_import", "name": "Bulk Import Students", "category": "STUDENT", "description": "Upload student rosters in bulk via Excel or CSV."},
    {"code": "students:enroll", "name": "Enroll Students", "category": "STUDENT", "description": "Enroll students into sessions and assign them to class arms."},
    {"code": "students:status", "name": "Update Student Status", "category": "STUDENT", "description": "Manage student lifecycle state transitions."},
    {"code": "subjects:register_electives", "name": "Register Elective Subjects", "category": "STUDENT", "description": "Register elective and trade subjects for students."},
    # Structure
    {"code": "structure:manage", "name": "Manage Academic Structure", "category": "STRUCTURE", "description": "Configure academic sessions, terms, levels, and class arms."},
    {"code": "subjects:manage", "name": "Manage Subjects", "category": "STRUCTURE", "description": "Define subjects, subject offerings, and class subject links."},
    {"code": "teachers:assign", "name": "Assign Teachers", "category": "STRUCTURE", "description": "Assign subject teachers and class form masters."},
    # Administration
    {"code": "users:manage", "name": "Manage Users & Staff", "category": "ADMIN", "description": "Invite staff, manage profiles, and deactivate accounts."},
    {"code": "permissions:delegate", "name": "Delegate Permissions", "category": "ADMIN", "description": "Grant scoped role delegations to teachers and coordinators."},
    {"code": "reports:publish", "name": "Publish Report Cards", "category": "ADMIN", "description": "Publish terminal and annual cumulative report cards."},
    {"code": "audit:view", "name": "View Audit Logs", "category": "ADMIN", "description": "Review system-wide security and computation audit logs."},
]

DEFAULT_ROLE_PERMISSIONS: Dict[str, List[str]] = {
    "TEACHER": [
        "scores:enter",
        "scores:modify",
        "scores:submit",
    ],
    "ACADEMIC_OFFICER": [
        "scores:enter",
        "scores:modify",
        "scores:submit",
        "scores:approve",
        "scores:unlock",
        "students:register",
        "students:bulk_import",
        "students:enroll",
        "students:status",
        "subjects:register_electives",
        "structure:manage",
        "subjects:manage",
        "teachers:assign",
        "users:manage",
    ],
}


async def seed_system_permissions(db: AsyncSession) -> None:
    """Idempotently seeds all system permissions and default role permissions."""
    code_to_perm: Dict[str, Permission] = {}

    for perm_data in SYSTEM_PERMISSIONS:
        stmt = select(Permission).where(Permission.code == perm_data["code"])
        result = await db.execute(stmt)
        perm = result.scalar_one_or_none()
        if not perm:
            perm = Permission(**perm_data)
            db.add(perm)
            await db.flush()
        code_to_perm[perm.code] = perm

    for role, perm_codes in DEFAULT_ROLE_PERMISSIONS.items():
        for code in perm_codes:
            perm = code_to_perm.get(code)
            if not perm:
                continue
            stmt = select(RoleDefaultPermission).where(
                RoleDefaultPermission.role == role,
                RoleDefaultPermission.permission_id == perm.id,
            )
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if not existing:
                db.add(RoleDefaultPermission(role=role, permission_id=perm.id))

    await db.commit()
