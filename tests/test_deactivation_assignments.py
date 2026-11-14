import uuid
from datetime import date
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import (
    async_session_factory,
    reset_tenant_scope,
    set_tenant_scope,
)
from app.main import app
from app.modules.academic_structure.models import (
    AcademicLevel,
    AcademicSession,
    ClassArm,
    ClassGroup,
)
from app.modules.identity.models import User
from app.modules.identity.seed import seed_system_permissions
from app.modules.subjects.models import (
    ClassSubject,
    Subject,
    SubjectOffering,
    TeacherAssignment,
)


@pytest.fixture(autouse=True)
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


@pytest.mark.asyncio
async def test_deactivation_revokes_assignments_and_form_master_role():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        school_payload = {
            "name": f"Olivet Baptist High School {suffix}",
            "slug": f"olivet-{suffix}",
            "official_code": f"OBHS-{suffix.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "Olivet Heights, Oyo, Nigeria",
            "country_code": "NG",
            "admin_email": f"principal.{suffix}@olivet-test.edu.ng",
            "admin_first_name": "Adeyemi",
            "admin_last_name": "Olufemi",
            "admin_password": "SuperAdminSecurePassword123!",
        }
        school_response = await client.post("/api/v1/identity/schools", json=school_payload)
        assert school_response.status_code == 201, school_response.text
        school_id = school_response.json()["data"]["id"]
        school_uuid = UUID(school_id)

        admin_login = await client.post(
            "/api/v1/identity/login",
            json={
                "email": school_payload["admin_email"],
                "password": school_payload["admin_password"],
            },
        )
        assert admin_login.status_code == 200
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['data']['access_token']}"}
        admin_me = await client.get("/api/v1/identity/me", headers=admin_headers)
        admin_user_id = UUID(admin_me.json()["data"]["id"])

        invite = await client.post(
            "/api/v1/identity/staff/invite",
            json={
                "email": f"teacher.{suffix}@olivet-test.edu.ng",
                "first_name": "Funke",
                "last_name": "Adebayo",
                "role": "TEACHER",
                "staff_id_number": f"STF-{suffix.upper()}",
            },
            headers=admin_headers,
        )
        assert invite.status_code == 201, invite.text
        accept = await client.post(
            "/api/v1/identity/staff/accept-invitation",
            json={
                "invitation_token": invite.json()["data"]["invitation_token"],
                "password": "TeacherStrongPassword2026!",
                "confirm_password": "TeacherStrongPassword2026!",
            },
        )
        assert accept.status_code == 200
        teacher_headers = {"Authorization": f"Bearer {accept.json()['data']['access_token']}"}
        teacher_me = await client.get("/api/v1/identity/me", headers=teacher_headers)
        teacher_user_id = UUID(teacher_me.json()["data"]["id"])

        tokens = set_tenant_scope(school_uuid)
        try:
            async with async_session_factory() as session:
                academic_session = AcademicSession(
                    school_id=school_uuid,
                    name=f"2024/2025-{suffix}",
                    start_date=date(2024, 9, 1),
                    end_date=date(2025, 7, 31),
                    is_current=True,
                )
                level = AcademicLevel(
                    school_id=school_uuid,
                    name="SS 1",
                    code=f"SS1-{suffix}",
                    level_order=4,
                    category="SENIOR_SECONDARY",
                )
                arm = ClassArm(school_id=school_uuid, name=f"O-{suffix}")
                session.add_all([academic_session, level, arm])
                await session.flush()

                class_group = ClassGroup(
                    school_id=school_uuid,
                    academic_session_id=academic_session.id,
                    academic_level_id=level.id,
                    class_arm_id=arm.id,
                    class_teacher_id=teacher_user_id,
                    display_name=f"SS1 Science O {suffix}",
                )
                subject = Subject(
                    school_id=school_uuid,
                    name="Chemistry",
                    code=f"CHM-{suffix}",
                )
                session.add_all([class_group, subject])
                await session.flush()

                offering = SubjectOffering(
                    school_id=school_uuid,
                    subject_id=subject.id,
                    academic_session_id=academic_session.id,
                    academic_level_id=level.id,
                )
                session.add(offering)
                await session.flush()

                class_subject = ClassSubject(
                    school_id=school_uuid,
                    subject_offering_id=offering.id,
                    class_group_id=class_group.id,
                )
                session.add(class_subject)
                await session.flush()

                assignment = TeacherAssignment(
                    school_id=school_uuid,
                    teacher_id=teacher_user_id,
                    class_subject_id=class_subject.id,
                    assigned_by_id=admin_user_id,
                    is_active=True,
                )
                session.add(assignment)
                await session.commit()
                class_group_id = class_group.id
                assignment_id = assignment.id
        finally:
            reset_tenant_scope(tokens)

        deactivate = await client.post(
            f"/api/v1/identity/users/{teacher_user_id}/deactivate",
            headers=admin_headers,
        )
        assert deactivate.status_code == 200, deactivate.text

        tokens = set_tenant_scope(school_uuid)
        try:
            async with async_session_factory() as session:
                assignment = await session.get(TeacherAssignment, assignment_id)
                class_group = await session.get(ClassGroup, class_group_id)
                assert assignment is not None
                assert assignment.is_active is False
                assert assignment.end_date == date.today()
                assert class_group is not None
                assert class_group.class_teacher_id is None

                user = (
                    await session.execute(select(User).where(User.id == teacher_user_id))
                ).scalar_one()
                assert user.is_active is False
        finally:
            reset_tenant_scope(tokens)
