import uuid
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import async_session_factory
from app.core.security import hash_password
from app.main import app
from app.modules.identity.models import User
from app.modules.identity.seed import seed_system_permissions


@pytest.fixture(autouse=True)
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


async def _create_school_and_admin(client: AsyncClient, suffix: str) -> tuple[str, str]:
    payload = {
        "name": f"Olivet Structure School {suffix}",
        "slug": f"olivet-structure-{suffix}",
        "official_code": f"OBHS-ST-{suffix.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": "Olivet Heights, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": f"structure.principal.{suffix}@olivet-test.edu.ng",
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
    email = f"structure.teacher.{suffix}@olivet-test.edu.ng"
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


async def _create_session(
    client: AsyncClient,
    headers: dict[str, str],
    name: str,
    start_date: str,
    end_date: str,
    *,
    is_current: bool = False,
) -> dict:
    response = await client.post(
        "/api/v1/structure/sessions",
        json={
            "name": name,
            "start_date": start_date,
            "end_date": end_date,
            "is_current": is_current,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def _create_level(
    client: AsyncClient,
    headers: dict[str, str],
    name: str,
    code: str,
    order: int,
    category: str,
) -> dict:
    response = await client.post(
        "/api/v1/structure/levels",
        json={"name": name, "code": code, "level_order": order, "category": category},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def _create_department(
    client: AsyncClient,
    headers: dict[str, str],
    name: str,
    code: str,
) -> dict:
    response = await client.post(
        "/api/v1/structure/departments",
        json={"name": name, "code": code},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def _create_arm(client: AsyncClient, headers: dict[str, str], name: str) -> dict:
    response = await client.post(
        "/api/v1/structure/arms",
        json={"name": name},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def _create_class_group(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    session_id: str,
    level_id: str,
    arm_id: str,
    department_id: str | None = None,
) -> dict:
    response = await client.post(
        "/api/v1/structure/class-groups",
        json={
            "academic_session_id": session_id,
            "academic_level_id": level_id,
            "department_id": department_id,
            "class_arm_id": arm_id,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


@pytest.mark.asyncio
async def test_structure_crud_current_switching_and_soft_retirement():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        _, token = await _create_school_and_admin(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}

        first_session = await _create_session(
            client,
            headers,
            "2024/2025",
            "2024-09-09",
            "2025-07-18",
            is_current=True,
        )
        second_session = await _create_session(
            client,
            headers,
            "2025/2026",
            "2025-09-08",
            "2026-07-17",
            is_current=True,
        )

        refreshed_first = await client.get(
            f"/api/v1/structure/sessions/{first_session['id']}",
            headers=headers,
        )
        assert refreshed_first.status_code == 200
        assert refreshed_first.json()["data"]["is_current"] is False
        assert second_session["is_current"] is True

        first_term = await client.post(
            f"/api/v1/structure/sessions/{first_session['id']}/terms",
            json={
                "name": "First Term",
                "term_order": 1,
                "start_date": "2024-09-09",
                "end_date": "2024-12-13",
                "is_current": True,
            },
            headers=headers,
        )
        assert first_term.status_code == 201, first_term.text

        second_term = await client.post(
            f"/api/v1/structure/sessions/{first_session['id']}/terms",
            json={
                "name": "Second Term",
                "term_order": 2,
                "start_date": "2025-01-06",
                "end_date": "2025-04-04",
                "is_current": True,
            },
            headers=headers,
        )
        assert second_term.status_code == 201, second_term.text
        second_term_id = second_term.json()["data"]["id"]

        junior_level = await _create_level(
            client,
            headers,
            "JSS 1",
            "JSS1",
            1,
            "JUNIOR_SECONDARY",
        )
        senior_level = await _create_level(
            client,
            headers,
            "SS 1",
            "SS1",
            4,
            "SENIOR_SECONDARY",
        )
        science = await _create_department(client, headers, "Science", "SCI")
        arm = await _create_arm(client, headers, "O")

        junior_group = await _create_class_group(
            client,
            headers,
            session_id=first_session["id"],
            level_id=junior_level["id"],
            arm_id=arm["id"],
        )
        assert junior_group["display_name"] == "JSS1 O"

        senior_group = await _create_class_group(
            client,
            headers,
            session_id=first_session["id"],
            level_id=senior_level["id"],
            department_id=science["id"],
            arm_id=arm["id"],
        )
        assert senior_group["display_name"] == "SS1 Science O"

        duplicate = await client.post(
            "/api/v1/structure/class-groups",
            json={
                "academic_session_id": first_session["id"],
                "academic_level_id": senior_level["id"],
                "department_id": science["id"],
                "class_arm_id": arm["id"],
            },
            headers=headers,
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "CLASS_GROUP_TAKEN"

        listed = await client.get(
            f"/api/v1/structure/class-groups?session_id={first_session['id']}&page=1&page_size=1",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()["data"]
        assert len(body["items"]) == 1
        assert body["meta"] == {
            "page": 1,
            "page_size": 1,
            "total": 2,
            "total_pages": 2,
        }

        current_term_retire = await client.delete(
            f"/api/v1/structure/terms/{second_term_id}",
            headers=headers,
        )
        assert current_term_retire.status_code == 422

        current_session_retire = await client.delete(
            f"/api/v1/structure/sessions/{second_session['id']}",
            headers=headers,
        )
        assert current_session_retire.status_code == 422

        retire_first_session = await client.delete(
            f"/api/v1/structure/sessions/{first_session['id']}",
            headers=headers,
        )
        assert retire_first_session.status_code == 200, retire_first_session.text
        missing_session = await client.get(
            f"/api/v1/structure/sessions/{first_session['id']}",
            headers=headers,
        )
        assert missing_session.status_code == 404

        retire_level = await client.delete(
            f"/api/v1/structure/levels/{junior_level['id']}",
            headers=headers,
        )
        assert retire_level.status_code == 200, retire_level.text

        reused_level = await _create_level(
            client,
            headers,
            "JSS 1",
            "JSS1",
            1,
            "JUNIOR_SECONDARY",
        )
        assert reused_level["id"] != junior_level["id"]


@pytest.mark.asyncio
async def test_structure_tenant_isolation_and_permission_denial():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        school_id, token = await _create_school_and_admin(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}
        session = await _create_session(
            client,
            headers,
            "2024/2025",
            "2024-09-09",
            "2025-07-18",
        )

        _, other_token = await _create_school_and_admin(client, f"other{suffix}")
        other_headers = {"Authorization": f"Bearer {other_token}"}
        cross_tenant = await client.get(
            f"/api/v1/structure/sessions/{session['id']}",
            headers=other_headers,
        )
        assert cross_tenant.status_code == 404
        assert cross_tenant.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

        teacher_email = await _create_teacher(school_id, suffix)
        login = await client.post(
            "/api/v1/identity/login",
            json={"email": teacher_email, "password": "TeacherSecurePassword123!"},
        )
        assert login.status_code == 200, login.text
        teacher_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        forbidden = await client.post(
            "/api/v1/structure/sessions",
            json={
                "name": "Teacher Session",
                "start_date": "2026-09-07",
                "end_date": "2027-07-16",
            },
            headers=teacher_headers,
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "INSUFFICIENT_PERMISSIONS"
        assert "permission" in forbidden.json()["error"]["message"].lower()
