import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import async_session_factory
from app.main import app
from app.modules.identity.seed import seed_system_permissions


@pytest.fixture(autouse=True)
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


async def _create_school(client: AsyncClient, suffix: str, label: str) -> dict:
    payload = {
        "name": f"Olivet Baptist High School {suffix} {label}",
        "slug": f"olivet-{suffix}-{label}",
        "official_code": f"OBHS-{suffix.upper()}-{label.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": f"Olivet Heights {label}, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": f"principal.{label}.{suffix}@olivet-test.edu.ng",
        "admin_first_name": "Admin",
        "admin_last_name": label.title(),
        "admin_password": "SuperAdminSecurePassword123!",
    }
    response = await client.post("/api/v1/identity/schools", json=payload)
    assert response.status_code == 201, response.text
    school = response.json()["data"]

    login = await client.post(
        "/api/v1/identity/login",
        json={
            "email": payload["admin_email"],
            "password": payload["admin_password"],
        },
    )
    assert login.status_code == 200, login.text
    return {
        "school": school,
        "admin_email": payload["admin_email"],
        "admin_password": payload["admin_password"],
        "access_token": login.json()["data"]["access_token"],
    }


@pytest.mark.asyncio
async def test_cross_tenant_login_isolation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        school_one = await _create_school(client, suffix, "one")
        school_two = await _create_school(client, suffix, "two")

        admin_one_headers = {"Authorization": f"Bearer {school_one['access_token']}"}
        admin_two_headers = {"Authorization": f"Bearer {school_two['access_token']}"}

        invite = await client.post(
            "/api/v1/identity/staff/invite",
            json={
                "email": f"teacher.{suffix}@olivet-test.edu.ng",
                "first_name": "Funke",
                "last_name": "Adebayo",
                "phone_number": "+2348031234567",
                "role": "TEACHER",
                "staff_id_number": f"STF-{suffix.upper()}",
                "specialization": "Chemistry",
            },
            headers=admin_two_headers,
        )
        assert invite.status_code == 201, invite.text
        invitation_token = invite.json()["data"]["invitation_token"]

        accept = await client.post(
            "/api/v1/identity/staff/accept-invitation",
            json={
                "invitation_token": invitation_token,
                "password": "TeacherStrongPassword2026!",
                "confirm_password": "TeacherStrongPassword2026!",
            },
        )
        assert accept.status_code == 200, accept.text
        teacher_token = accept.json()["data"]["access_token"]
        teacher_headers = {"Authorization": f"Bearer {teacher_token}"}

        teacher_me = await client.get("/api/v1/identity/me", headers=teacher_headers)
        assert teacher_me.status_code == 200, teacher_me.text
        teacher = teacher_me.json()["data"]
        assert teacher["school_id"] == school_two["school"]["id"]

        cross_grant = await client.post(
            f"/api/v1/identity/users/{teacher['id']}/permissions",
            json={
                "permission_code": "scores:approve",
                "scope_type": "SCHOOL_WIDE",
            },
            headers=admin_one_headers,
        )
        assert cross_grant.status_code == 404, cross_grant.text

        cross_deactivate = await client.post(
            f"/api/v1/identity/users/{teacher['id']}/deactivate",
            headers=admin_one_headers,
        )
        assert cross_deactivate.status_code == 404, cross_deactivate.text

        admin_one_me = await client.get("/api/v1/identity/me", headers=admin_one_headers)
        assert admin_one_me.status_code == 200
        assert admin_one_me.json()["data"]["school_id"] == school_one["school"]["id"]

        admin_two_me = await client.get("/api/v1/identity/me", headers=admin_two_headers)
        assert admin_two_me.status_code == 200
        assert admin_two_me.json()["data"]["school_id"] == school_two["school"]["id"]
