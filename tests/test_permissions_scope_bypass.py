import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import (
    async_session_factory,
    reset_tenant_scope,
    set_tenant_scope,
)
from app.core.exceptions import InsufficientPermissionException
from app.core.permissions import require_permission
from app.core.security import hash_password
from app.main import app
from app.modules.academic_structure.models import Department
from app.modules.identity.models import Permission, User, UserPermissionGrant
from app.modules.identity.seed import seed_system_permissions


@pytest.fixture(autouse=True)
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


async def _create_school(client: AsyncClient, suffix: str) -> uuid.UUID:
    payload = {
        "name": f"Scope Bypass School {suffix}",
        "slug": f"scope-bypass-{suffix}",
        "official_code": f"SCB-{suffix.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": "Olivet Heights, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": f"admin.{suffix}@olivet-test.edu.ng",
        "admin_first_name": "Admin",
        "admin_last_name": "User",
        "admin_password": "SuperAdminSecurePassword123!",
    }
    response = await client.post("/api/v1/identity/schools", json=payload)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["data"]["id"])


@pytest.mark.asyncio
async def test_department_scoped_grant_does_not_satisfy_unrelated_class_group_check():
    """A grant scoped to one dimension must never satisfy a check for a different,
    unsupplied dimension just because both happen to be NULL on that grant row."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id = await _create_school(client, suffix)

    tokens = set_tenant_scope(school_id)
    try:
        async with async_session_factory() as session:
            admin = (
                (await session.execute(select(User).where(User.school_id == school_id)))
                .scalars()
                .first()
            )

            department = Department(school_id=school_id, name="Science", code=f"SCI{suffix}")
            session.add(department)
            await session.flush()

            teacher = User(
                school_id=school_id,
                email=f"teacher.{suffix}@olivet-test.edu.ng",
                password_hash=hash_password("irrelevant"),
                first_name="Funke",
                last_name="Adebayo",
                role="TEACHER",
                is_active=True,
            )
            session.add(teacher)
            await session.flush()

            permission = (
                await session.execute(
                    select(Permission).where(Permission.code == "teachers:assign")
                )
            ).scalar_one()

            grant = UserPermissionGrant(
                school_id=school_id,
                user_id=teacher.id,
                permission_id=permission.id,
                granted_by_id=admin.id,
                scope_type="DEPARTMENT",
                department_id=department.id,
            )
            session.add(grant)
            await session.commit()

            checker = require_permission(
                "teachers:assign",
                scope_extractor=lambda _request: {
                    "department_id": None,
                    "academic_level_id": None,
                    "class_group_id": uuid.uuid4(),
                },
            )
            with pytest.raises(InsufficientPermissionException):
                await checker(None, current_user=teacher, db=session)

            matching_checker = require_permission(
                "teachers:assign",
                scope_extractor=lambda _request: {
                    "department_id": department.id,
                    "academic_level_id": None,
                    "class_group_id": None,
                },
            )
            assert await matching_checker(None, current_user=teacher, db=session) is True
    finally:
        reset_tenant_scope(tokens)
