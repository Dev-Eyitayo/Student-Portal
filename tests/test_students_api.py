import uuid
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.database import async_session_factory
from app.core.security import hash_password
from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.identity.models import User
from app.modules.identity.seed import seed_system_permissions


@pytest.fixture(autouse=True)
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


async def _create_school_and_admin(client: AsyncClient, suffix: str) -> tuple[str, str]:
    payload = {
        "name": f"Olivet Student School {suffix}",
        "slug": f"olivet-student-{suffix}",
        "official_code": f"OBHS-STU-{suffix.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": "Olivet Heights, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": f"student.principal.{suffix}@olivet-test.edu.ng",
        "admin_first_name": "Adeyemi",
        "admin_last_name": "Olufemi",
        "admin_password": "SuperAdminSecurePassword123!",
    }
    response = await client.post("/api/v1/identity/schools", json=payload)
    assert response.status_code == 201, response.text
    school_id = response.json()["data"]["id"]

    login = await client.post(
        "/api/v1/identity/login",
        json={
            "email": payload["admin_email"],
            "password": payload["admin_password"],
        },
    )
    assert login.status_code == 200, login.text
    return school_id, login.json()["data"]["access_token"]


async def _create_teacher(school_id: str, suffix: str) -> str:
    email = f"students.teacher.{suffix}@olivet-test.edu.ng"
    password = "TeacherSecurePassword123!"
    async with async_session_factory() as session:
        session.add(
            User(
                school_id=UUID(school_id),
                email=email,
                password_hash=hash_password(password),
                first_name="Funke",
                last_name="Adebayo",
                role="TEACHER",
                is_active=True,
            )
        )
        await session.commit()
    return email


async def _post(client: AsyncClient, path: str, payload: dict, headers: dict) -> dict:
    response = await client.post(path, json=payload, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


async def _setup_class_group(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    session_name: str,
    start_date: str,
    end_date: str,
    level_code: str,
    level_order: int,
    is_current: bool,
) -> tuple[dict, dict, dict, dict]:
    session = await _post(
        client,
        "/api/v1/structure/sessions",
        {
            "name": session_name,
            "start_date": start_date,
            "end_date": end_date,
            "is_current": is_current,
        },
        headers,
    )
    level = await _post(
        client,
        "/api/v1/structure/levels",
        {
            "name": f"JSS {level_order}",
            "code": level_code,
            "level_order": level_order,
            "category": "JUNIOR_SECONDARY",
        },
        headers,
    )
    arm = await _post(client, "/api/v1/structure/arms", {"name": "O"}, headers)
    class_group = await _post(
        client,
        "/api/v1/structure/class-groups",
        {
            "academic_session_id": session["id"],
            "academic_level_id": level["id"],
            "class_arm_id": arm["id"],
        },
        headers,
    )
    return session, class_group, level, arm


@pytest.mark.asyncio
async def test_student_lifecycle_and_enrollment_history():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        school_id, token = await _create_school_and_admin(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}

        first_session, first_group, level, arm = await _setup_class_group(
            client,
            headers,
            session_name="2024/2025",
            start_date="2024-09-09",
            end_date="2025-07-18",
            level_code="JSS1",
            level_order=1,
            is_current=True,
        )
        second_session = await _post(
            client,
            "/api/v1/structure/sessions",
            {
                "name": "2025/2026",
                "start_date": "2025-09-08",
                "end_date": "2026-07-17",
                "is_current": True,
            },
            headers,
        )
        second_group = await _post(
            client,
            "/api/v1/structure/class-groups",
            {
                "academic_session_id": second_session["id"],
                "academic_level_id": level["id"],
                "class_arm_id": arm["id"],
            },
            headers,
        )

        student = await _post(
            client,
            "/api/v1/students",
            {
                "admission_number": f"OBHS/{suffix}/0001",
                "first_name": "Tunde",
                "middle_name": "Oluwaseun",
                "last_name": "Bello",
                "date_of_birth": "2012-01-15",
                "gender": "MALE",
                "guardian_name": "Mr Bello",
                "guardian_email": f"guardian.{suffix}@olivet-test.edu.ng",
                "guardian_phone": "08031234567",
                "enrollment_date": "2024-09-09",
            },
            headers,
        )
        assert student["status"] == "ADMITTED"
        assert student["gender"] == "M"

        updated = await client.patch(
            f"/api/v1/students/{student['id']}",
            json={"guardian_phone": "08039876543"},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["guardian_phone"] == "08039876543"

        enrollment = await _post(
            client,
            f"/api/v1/students/{student['id']}/enrollments",
            {"class_group_id": first_group["id"]},
            headers,
        )
        assert enrollment["enrollment_status"] == "ENROLLED"
        assert enrollment["class_group"]["display_name"] == "JSS1 O"
        assert enrollment["academic_session"]["name"] == first_session["name"]

        activated = await client.get(f"/api/v1/students/{student['id']}", headers=headers)
        assert activated.status_code == 200
        assert activated.json()["data"]["status"] == "ACTIVE"

        duplicate_enrollment = await client.post(
            f"/api/v1/students/{student['id']}/enrollments",
            json={"class_group_id": first_group["id"]},
            headers=headers,
        )
        assert duplicate_enrollment.status_code == 409

        search = await client.get(
            f"/api/v1/students?q={suffix}&status=ACTIVE",
            headers=headers,
        )
        assert search.status_code == 200, search.text
        assert search.json()["data"]["meta"]["total"] == 1

        suspended = await client.post(
            f"/api/v1/students/{student['id']}/status",
            json={"status": "SUSPENDED", "reason": "Pending disciplinary review"},
            headers=headers,
        )
        assert suspended.status_code == 200, suspended.text
        assert suspended.json()["data"]["status"] == "SUSPENDED"

        reactivated = await client.post(
            f"/api/v1/students/{student['id']}/status",
            json={"status": "ACTIVE", "reason": "Disciplinary review completed"},
            headers=headers,
        )
        assert reactivated.status_code == 200, reactivated.text

        promoted = await client.patch(
            f"/api/v1/enrollments/{enrollment['id']}",
            json={
                "enrollment_status": "PROMOTED",
                "promoted_to_class_group_id": second_group["id"],
                "reason": "End of session promotion",
            },
            headers=headers,
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["data"]["enrollment_status"] == "PROMOTED"
        assert promoted.json()["data"]["promoted_to_class_group_id"] == second_group["id"]

        second_enrollment = await _post(
            client,
            f"/api/v1/students/{student['id']}/enrollments",
            {"class_group_id": second_group["id"]},
            headers,
        )
        assert second_enrollment["academic_session"]["id"] == second_session["id"]

        terminal_update = await client.patch(
            f"/api/v1/enrollments/{enrollment['id']}",
            json={"enrollment_status": "WITHDRAWN"},
            headers=headers,
        )
        assert terminal_update.status_code == 422

        history = await client.get(
            f"/api/v1/students/{student['id']}/enrollments",
            headers=headers,
        )
        assert history.status_code == 200, history.text
        history_items = history.json()["data"]["items"]
        assert len(history_items) == 2
        assert history_items[0]["id"] == second_enrollment["id"]
        assert history_items[0]["academic_session"]["id"] == second_session["id"]
        assert history_items[1]["id"] == enrollment["id"]
        assert history_items[1]["academic_session"]["id"] == first_session["id"]

        withdrawn = await client.post(
            f"/api/v1/students/{student['id']}/status",
            json={"status": "WITHDRAWN", "reason": "Family relocated to another state"},
            headers=headers,
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["data"]["deleted_at"] is not None

        default_list = await client.get(f"/api/v1/students?q={suffix}", headers=headers)
        assert default_list.json()["data"]["meta"]["total"] == 0
        archived_list = await client.get(
            f"/api/v1/students?q={suffix}&include_archived=true",
            headers=headers,
        )
        assert archived_list.json()["data"]["meta"]["total"] == 1

        still_viewable = await client.get(
            f"/api/v1/students/{student['id']}",
            headers=headers,
        )
        assert still_viewable.status_code == 200

        async with async_session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.current_school_id', :school_id, true)"),
                {"school_id": school_id},
            )
            audit_actions = set(
                (
                    await session.execute(
                        select(AuditLog.action).where(
                            AuditLog.school_id == UUID(school_id),
                            AuditLog.resource_id.in_(
                                [UUID(str(student["id"])), UUID(str(enrollment["id"]))]
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert "STUDENT_CREATED" in audit_actions
            assert "STUDENT_ENROLLED" in audit_actions
            assert "STUDENT_STATUS_CHANGED" in audit_actions
            assert "ENROLLMENT_STATUS_CHANGED" in audit_actions


@pytest.mark.asyncio
async def test_student_tenant_isolation_and_permission_denial():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        school_id, token = await _create_school_and_admin(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}

        _, class_group, _, _ = await _setup_class_group(
            client,
            headers,
            session_name="2024/2025",
            start_date="2024-09-09",
            end_date="2025-07-18",
            level_code="JSS1",
            level_order=1,
            is_current=True,
        )
        student = await _post(
            client,
            "/api/v1/students",
            {
                "admission_number": f"OBHS/TENANT/{suffix}",
                "first_name": "Tunde",
                "last_name": "Bello",
                "date_of_birth": "2012-01-15",
                "gender": "M",
                "guardian_name": "Mr Bello",
                "guardian_email": f"tenant.guardian.{suffix}@olivet-test.edu.ng",
                "guardian_phone": "08031234567",
                "enrollment_date": "2024-09-09",
            },
            headers,
        )
        enrollment = await _post(
            client,
            f"/api/v1/students/{student['id']}/enrollments",
            {"class_group_id": class_group["id"]},
            headers,
        )

        _, other_token = await _create_school_and_admin(client, f"other{suffix}")
        other_headers = {"Authorization": f"Bearer {other_token}"}

        cross_tenant_student = await client.get(
            f"/api/v1/students/{student['id']}",
            headers=other_headers,
        )
        assert cross_tenant_student.status_code == 404

        cross_tenant_enrollment = await client.get(
            f"/api/v1/enrollments/{enrollment['id']}",
            headers=other_headers,
        )
        assert cross_tenant_enrollment.status_code == 404

        teacher_email = await _create_teacher(school_id, suffix)
        login = await client.post(
            "/api/v1/identity/login",
            json={"email": teacher_email, "password": "TeacherSecurePassword123!"},
        )
        assert login.status_code == 200, login.text
        teacher_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        forbidden = await client.post(
            "/api/v1/students",
            json={
                "admission_number": f"OBHS/TEACHER/{suffix}",
                "first_name": "Funke",
                "last_name": "Adebayo",
                "date_of_birth": "2011-01-15",
                "gender": "F",
                "guardian_name": "Mrs Adebayo",
                "guardian_email": f"teacher.guardian.{suffix}@olivet-test.edu.ng",
                "guardian_phone": "08031234567",
                "enrollment_date": "2024-09-09",
            },
            headers=teacher_headers,
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "INSUFFICIENT_PERMISSIONS"
