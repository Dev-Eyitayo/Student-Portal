import json
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import async_session_factory
from app.core.security import create_access_token
from app.main import app
from app.modules.identity.models import School, SchoolConfiguration, StaffProfile, User
from app.modules.identity.schemas import SchoolType
from app.modules.identity.seed import seed_system_permissions
from app.modules.identity.services import SCHOOL_DEFAULTS

# Minimal valid 1x1 PNG bytes
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00"
    b"\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


async def _create_school_and_admin(suffix: str) -> tuple[School, User, str]:
    async with async_session_factory() as db:
        school = School(
            slug=f"gm-school-{suffix.lower()}-{uuid4().hex[:6]}",
            name=f"GM Test School {suffix}",
            official_code=f"GMS-{suffix}-{uuid4().hex[:4]}",
            country_code="NG",
            state="Oyo",
            city="Oyo",
            address="10 School Road",
            school_type="SECONDARY",
            is_active=True,
        )
        db.add(school)
        await db.flush()

        defaults = SCHOOL_DEFAULTS[SchoolType.SECONDARY]
        db.add(
            SchoolConfiguration(
                school_id=school.id,
                grading_scale_schema=defaults["grading_scale_schema"],
                assessment_weight_schema=defaults["assessment_weight_schema"],
                academic_term_definitions=defaults["academic_term_definitions"],
                annual_results_schema=defaults["annual_results_schema"],
                timezone="Africa/Lagos",
                currency="NGN",
                report_card_template_id=defaults["report_card_template_id"],
            )
        )

        admin = User(
            school_id=school.id,
            email=f"admin-{suffix.lower()}-{uuid4().hex[:6]}@olivet.edu.ng",
            password_hash="not-used-in-token-test",
            role="SCHOOL_ADMIN",
            first_name="Admin",
            last_name=suffix,
            is_active=True,
        )
        db.add(admin)
        await db.flush()
        db.add(
            StaffProfile(
                school_id=school.id,
                user_id=admin.id,
                staff_id_number=f"STF-{suffix}-{uuid4().hex[:4]}",
                employment_status="ACTIVE",
            )
        )
        await db.commit()
        token = create_access_token(subject=str(admin.id), school_id=school.id, role=admin.role)
        return school, admin, token


@pytest.mark.asyncio
async def test_guardian_deduplication_directory_and_tenant_isolation():
    _, _, token_a = await _create_school_and_admin("A")
    _, _, token_b = await _create_school_and_admin("B")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create first sibling
        r1 = await client.post(
            "/api/v1/students",
            headers=headers_a,
            json={
                "admission_number": "GMS/2026/001",
                "first_name": "Tunde",
                "last_name": "Okafor",
                "date_of_birth": "2012-05-10",
                "gender": "M",
                "guardian_name": "Mr. Chidi Okafor",
                "guardian_email": "chidi.okafor@olivet.edu.ng",
                "guardian_phone": "08031234567",
                "guardian_relationship": "FATHER",
                "enrollment_date": "2026-09-14",
            },
        )
        assert r1.status_code == 201, r1.text
        stu1 = r1.json()["data"]
        guardian_id = stu1["primary_guardian_id"]
        assert guardian_id is not None

        # 2. Create second sibling with same phone (should deduplicate to same guardian_id)
        r2 = await client.post(
            "/api/v1/students",
            headers=headers_a,
            json={
                "admission_number": "GMS/2026/002",
                "first_name": "Ngozi",
                "last_name": "Okafor",
                "date_of_birth": "2014-03-20",
                "gender": "F",
                "guardian_name": "Chidi Okafor",
                "guardian_email": "chidi.okafor@olivet.edu.ng",
                "guardian_phone": "+2348031234567",
                "guardian_relationship": "FATHER",
                "enrollment_date": "2026-09-14",
            },
        )
        assert r2.status_code == 201, r2.text
        stu2 = r2.json()["data"]
        assert stu2["primary_guardian_id"] == guardian_id

        # 3. Parents directory lists 1 guardian with 2 wards
        g_list = await client.get("/api/v1/students/guardians", headers=headers_a)
        assert g_list.status_code == 200
        g_data = g_list.json()["data"]
        assert g_data["summary"]["total_guardians"] == 1
        assert g_data["summary"]["multi_ward_guardians"] == 1
        assert g_data["items"][0]["wards_count"] == 2

        # 4. Updating guardian syncs both wards' guardian_name
        g_patch = await client.patch(
            f"/api/v1/students/guardians/{guardian_id}",
            headers=headers_a,
            json={"full_name": "Chief Chidi Okafor", "occupation": "Civil Engineer"},
        )
        assert g_patch.status_code == 200
        assert g_patch.json()["data"]["full_name"] == "Chief Chidi Okafor"

        stu1_refetch = await client.get(f"/api/v1/students/{stu1['id']}", headers=headers_a)
        assert stu1_refetch.json()["data"]["guardian_name"] == "Chief Chidi Okafor"

        # 5. Cross-tenant isolation: School B cannot read School A's guardian
        cross_get = await client.get(f"/api/v1/students/guardians/{guardian_id}", headers=headers_b)
        assert cross_get.status_code == 404


@pytest.mark.asyncio
async def test_student_medical_profile_optional_and_isolated():
    _, _, token_a = await _create_school_and_admin("MA")
    _, _, token_b = await _create_school_and_admin("MB")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_stu = await client.post(
            "/api/v1/students",
            headers=headers_a,
            json={
                "admission_number": "MED/2026/001",
                "first_name": "Amina",
                "last_name": "Yusuf",
                "date_of_birth": "2013-02-14",
                "gender": "F",
                "guardian_name": "Halima Yusuf",
                "guardian_email": "halima.yusuf@olivet.edu.ng",
                "guardian_phone": "08055551234",
                "enrollment_date": "2026-09-14",
            },
        )
        assert r_stu.status_code == 201
        student_id = r_stu.json()["data"]["id"]

        # Initially optional (has_record = False)
        r_empty = await client.get(
            f"/api/v1/students/{student_id}/medical-profile", headers=headers_a
        )
        assert r_empty.status_code == 200
        assert r_empty.json()["data"]["has_record"] is False

        # Upsert medical profile
        r_put = await client.put(
            f"/api/v1/students/{student_id}/medical-profile",
            headers=headers_a,
            json={
                "blood_group": "O+",
                "genotype": "AA",
                "allergies": "Peanuts",
                "emergency_contact_name": "Dr. Musa",
                "emergency_contact_phone": "08099998888",
            },
        )
        assert r_put.status_code == 200
        data = r_put.json()["data"]
        assert data["has_record"] is True
        assert data["blood_group"] == "O+"
        assert data["genotype"] == "AA"
        assert data["emergency_contact_phone"] == "08099998888"

        # Cross-tenant denial
        r_cross = await client.get(
            f"/api/v1/students/{student_id}/medical-profile", headers=headers_b
        )
        assert r_cross.status_code == 404


@pytest.mark.asyncio
async def test_multipart_school_setup_and_teacher_signature():
    school, _, token = await _create_school_and_admin("MP")
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Multipart PATCH /identity/schools/{id}/setup with logo + principal_signature
        setup_res = await client.patch(
            f"/api/v1/identity/schools/{school.id}/setup",
            headers=headers,
            data={"payload": json.dumps({"profile": {"city": "Ibadan"}})},
            files={
                "logo": ("logo.png", TINY_PNG, "image/png"),
                "principal_signature": ("sig.png", TINY_PNG, "image/png"),
            },
        )
        assert setup_res.status_code == 200, setup_res.text
        setup_data = setup_res.json()["data"]
        assert setup_data["school"]["city"] == "Ibadan"
        assert setup_data["branding"]["logo_url"] is not None
        assert setup_data["branding"]["principal_signature_url"] is not None

        # 2. Multipart PATCH /identity/me with teacher signature
        me_res = await client.patch(
            "/api/v1/identity/me",
            headers=headers,
            data={"payload": json.dumps({"first_name": "UpdatedAdmin"})},
            files={"signature": ("teacher_sig.png", TINY_PNG, "image/png")},
        )
        assert me_res.status_code == 200, me_res.text
        me_data = me_res.json()["data"]
        assert me_data["first_name"] == "UpdatedAdmin"
        assert me_data["staff_profile"]["signature_image_url"] is not None
