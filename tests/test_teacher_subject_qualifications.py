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


async def _create_school_and_admin(client: AsyncClient, suffix: str) -> tuple[str, dict]:
    payload = {
        "name": f"Olivet Qualifications School {suffix}",
        "slug": f"olivet-qual-{suffix}",
        "official_code": f"OBHS-QUAL-{suffix.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": "Olivet Heights, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": f"qual.principal.{suffix}@olivet-test.edu.ng",
        "admin_first_name": "Adeyemi",
        "admin_last_name": "Olufemi",
        "admin_password": "SuperAdminSecurePassword123!",
    }
    response = await client.post("/api/v1/identity/schools", json=payload)
    assert response.status_code == 201, response.text
    school_id = response.json()["data"]["id"]

    login = await client.post(
        "/api/v1/identity/login",
        json={"email": payload["admin_email"], "password": payload["admin_password"]},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    return school_id, headers


async def _create_teacher(school_id: str, suffix: str, *, label: str = "a") -> str:
    email = f"qual.teacher.{label}.{suffix}@olivet-test.edu.ng"
    user_id: UUID
    async with async_session_factory() as session:
        user = User(
            school_id=UUID(school_id),
            email=email,
            password_hash=hash_password("TeacherSecurePassword123!"),
            first_name="Funke",
            last_name="Adebayo",
            role="TEACHER",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        user_id = user.id
    return str(user_id)


async def _create_subject(
    client: AsyncClient, headers: dict, suffix: str, *, label: str = "MTH"
) -> str:
    response = await client.post(
        "/api/v1/subjects",
        json={"name": f"Mathematics {suffix}", "code": f"{label}{suffix}"[:16]},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


@pytest.mark.asyncio
async def test_set_and_fetch_teacher_subject_qualifications():
    suffix = uuid.uuid4().hex[:6]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        school_id, headers = await _create_school_and_admin(client, suffix)
        teacher_id = await _create_teacher(school_id, suffix)
        subject_a = await _create_subject(client, headers, suffix, label="MTA")
        subject_b = await _create_subject(client, headers, suffix, label="MTB")

        # Initially no qualifications.
        get_resp = await client.get(
            f"/api/v1/identity/users/{teacher_id}/subject-qualifications", headers=headers
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["data"]["subjects"] == []

        # Set two subjects.
        put_resp = await client.put(
            f"/api/v1/identity/users/{teacher_id}/subject-qualifications",
            json={"subject_ids": [subject_a, subject_b]},
            headers=headers,
        )
        assert put_resp.status_code == 200, put_resp.text
        returned_ids = {s["id"] for s in put_resp.json()["data"]["subjects"]}
        assert returned_ids == {subject_a, subject_b}

        # The qualified-teachers pool for subject_a should include this teacher.
        pool_resp = await client.get(
            f"/api/v1/subjects/{subject_a}/qualified-teachers", headers=headers
        )
        assert pool_resp.status_code == 200, pool_resp.text
        pool_ids = {t["teacher_id"] for t in pool_resp.json()["data"]}
        assert teacher_id in pool_ids

        # Replace-all semantics: dropping subject_a, keeping subject_b.
        put_resp_2 = await client.put(
            f"/api/v1/identity/users/{teacher_id}/subject-qualifications",
            json={"subject_ids": [subject_b]},
            headers=headers,
        )
        assert put_resp_2.status_code == 200, put_resp_2.text
        returned_ids_2 = {s["id"] for s in put_resp_2.json()["data"]["subjects"]}
        assert returned_ids_2 == {subject_b}

        pool_resp_2 = await client.get(
            f"/api/v1/subjects/{subject_a}/qualified-teachers", headers=headers
        )
        assert pool_resp_2.status_code == 200, pool_resp_2.text
        assert teacher_id not in {t["teacher_id"] for t in pool_resp_2.json()["data"]}


@pytest.mark.asyncio
async def test_teacher_subject_qualifications_reject_subject_outside_school():
    suffix_a = uuid.uuid4().hex[:6]
    suffix_b = uuid.uuid4().hex[:6]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        school_a, headers_a = await _create_school_and_admin(client, suffix_a)
        _, headers_b = await _create_school_and_admin(client, suffix_b)
        teacher_a = await _create_teacher(school_a, suffix_a)
        subject_b = await _create_subject(client, headers_b, suffix_b, label="SCB")

        # School A cannot tag its teacher with a subject that belongs to School B.
        put_resp = await client.put(
            f"/api/v1/identity/users/{teacher_a}/subject-qualifications",
            json={"subject_ids": [subject_b]},
            headers=headers_a,
        )
        assert put_resp.status_code == 422, put_resp.text


@pytest.mark.asyncio
async def test_teacher_subject_qualifications_tenant_isolation():
    suffix_a = uuid.uuid4().hex[:6]
    suffix_b = uuid.uuid4().hex[:6]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        school_a, headers_a = await _create_school_and_admin(client, suffix_a)
        _, headers_b = await _create_school_and_admin(client, suffix_b)
        teacher_a = await _create_teacher(school_a, suffix_a)
        subject_a = await _create_subject(client, headers_a, suffix_a, label="MCA")

        await client.put(
            f"/api/v1/identity/users/{teacher_a}/subject-qualifications",
            json={"subject_ids": [subject_a]},
            headers=headers_a,
        )

        # School B's admin cannot read School A's teacher's qualifications --
        # the teacher lookup itself is tenant-scoped, so this comes back as a
        # "staff member not found in your school" validation error (422)
        # rather than leaking a 403/404 that would confirm the ID exists.
        cross_get = await client.get(
            f"/api/v1/identity/users/{teacher_a}/subject-qualifications", headers=headers_b
        )
        assert cross_get.status_code == 422, cross_get.text

        # School B's admin cannot see School A's teacher in School A's subject
        # pool either (the subject itself isn't visible cross-tenant).
        cross_pool = await client.get(
            f"/api/v1/subjects/{subject_a}/qualified-teachers", headers=headers_b
        )
        assert cross_pool.status_code in (403, 404), cross_pool.text


@pytest.mark.asyncio
async def test_staff_profile_detail_access_and_tenant_isolation():
    suffix_a = uuid.uuid4().hex[:6]
    suffix_b = uuid.uuid4().hex[:6]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        school_a, headers_a = await _create_school_and_admin(client, suffix_a)
        _, headers_b = await _create_school_and_admin(client, suffix_b)
        teacher_a = await _create_teacher(school_a, suffix_a, label="prof_a")
        teacher_b = await _create_teacher(school_a, suffix_a, label="prof_b")
        subject_a = await _create_subject(client, headers_a, suffix_a, label="PRF")

        await client.put(
            f"/api/v1/identity/users/{teacher_a}/subject-qualifications",
            json={"subject_ids": [subject_a]},
            headers=headers_a,
        )

        # Admin can view full staff profile detail.
        admin_resp = await client.get(f"/api/v1/identity/users/{teacher_a}", headers=headers_a)
        assert admin_resp.status_code == 200, admin_resp.text
        data = admin_resp.json()["data"]
        assert data["id"] == teacher_a
        assert [s["subject_id"] for s in data["qualified_subjects"]] == [subject_a]

        # Teacher can view their own staff profile.
        login_a = await client.post(
            "/api/v1/identity/login",
            json={
                "email": f"qual.teacher.prof_a.{suffix_a}@olivet-test.edu.ng",
                "password": "TeacherSecurePassword123!",
            },
        )
        assert login_a.status_code == 200, login_a.text
        headers_teacher_a = {"Authorization": f"Bearer {login_a.json()['data']['access_token']}"}
        self_resp = await client.get(
            f"/api/v1/identity/users/{teacher_a}", headers=headers_teacher_a
        )
        assert self_resp.status_code == 200, self_resp.text

        # Another teacher without users:manage cannot view teacher_a's profile.
        login_b = await client.post(
            "/api/v1/identity/login",
            json={
                "email": f"qual.teacher.prof_b.{suffix_a}@olivet-test.edu.ng",
                "password": "TeacherSecurePassword123!",
            },
        )
        assert login_b.status_code == 200, login_b.text
        headers_teacher_b = {"Authorization": f"Bearer {login_b.json()['data']['access_token']}"}
        assert teacher_b != teacher_a
        peer_resp = await client.get(
            f"/api/v1/identity/users/{teacher_a}", headers=headers_teacher_b
        )
        assert peer_resp.status_code == 403, peer_resp.text

        # Cross-tenant admin cannot view School A's staff profile.
        cross_resp = await client.get(f"/api/v1/identity/users/{teacher_a}", headers=headers_b)
        assert cross_resp.status_code in (403, 404), cross_resp.text


@pytest.mark.asyncio
async def test_teacher_level_category_scoping():
    suffix = uuid.uuid4().hex[:6]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        school_id, headers = await _create_school_and_admin(client, suffix)
        teacher_id = await _create_teacher(school_id, suffix, label="lvl")
        subject_id = await _create_subject(client, headers, suffix, label="LVL")

        put_resp = await client.put(
            f"/api/v1/identity/users/{teacher_id}/subject-qualifications",
            json={
                "subject_ids": [subject_id],
                "level_categories": ["JUNIOR_SECONDARY", "SENIOR_SECONDARY"],
            },
            headers=headers,
        )
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["data"]["level_categories"] == [
            "JUNIOR_SECONDARY",
            "SENIOR_SECONDARY",
        ]

        # Returned on qualified-teachers pool endpoint for the subject.
        pool_resp = await client.get(
            f"/api/v1/subjects/{subject_id}/qualified-teachers", headers=headers
        )
        assert pool_resp.status_code == 200, pool_resp.text
        pool_items = pool_resp.json()["data"]
        assert len(pool_items) == 1
        assert pool_items[0]["level_categories"] == ["JUNIOR_SECONDARY", "SENIOR_SECONDARY"]

        # Returned on staff profile detail endpoint.
        prof_resp = await client.get(f"/api/v1/identity/users/{teacher_id}", headers=headers)
        assert prof_resp.status_code == 200, prof_resp.text
        assert prof_resp.json()["data"]["level_categories"] == [
            "JUNIOR_SECONDARY",
            "SENIOR_SECONDARY",
        ]
