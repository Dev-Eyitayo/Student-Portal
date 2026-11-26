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
        "name": f"Olivet Subjects School {suffix}",
        "slug": f"olivet-subjects-{suffix}",
        "official_code": f"OBHS-SUB-{suffix.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": "Olivet Heights, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": f"subjects.principal.{suffix}@olivet-test.edu.ng",
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
    return school_id, login.json()["data"]["access_token"]


async def _create_teacher(school_id: str, suffix: str, *, label: str = "a") -> tuple[str, str]:
    email = f"subjects.teacher.{label}.{suffix}@olivet-test.edu.ng"
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
    return email, password


async def _post(client: AsyncClient, path: str, payload: dict, headers: dict) -> dict:
    response = await client.post(path, json=payload, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    response = await client.post(
        "/api/v1/identity/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def _setup_structure(client: AsyncClient, headers: dict, suffix: str) -> dict:
    session = await _post(
        client,
        "/api/v1/structure/sessions",
        {
            "name": f"2024/2025 {suffix}",
            "start_date": "2024-09-09",
            "end_date": "2025-07-18",
            "is_current": True,
        },
        headers,
    )
    term = await _post(
        client,
        f"/api/v1/structure/sessions/{session['id']}/terms",
        {
            "name": "First Term",
            "term_order": 1,
            "start_date": "2024-09-09",
            "end_date": "2024-12-13",
            "is_current": True,
        },
        headers,
    )
    level = await _post(
        client,
        "/api/v1/structure/levels",
        {
            "name": "JSS 1",
            "code": f"JSS1{suffix}",
            "level_order": 1,
            "category": "JUNIOR_SECONDARY",
        },
        headers,
    )
    arm = await _post(client, "/api/v1/structure/arms", {"name": f"O{suffix}"}, headers)
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
    second_arm = await _post(client, "/api/v1/structure/arms", {"name": f"L{suffix}"}, headers)
    second_class_group = await _post(
        client,
        "/api/v1/structure/class-groups",
        {
            "academic_session_id": session["id"],
            "academic_level_id": level["id"],
            "class_arm_id": second_arm["id"],
        },
        headers,
    )
    return {
        "session": session,
        "term": term,
        "level": level,
        "class_group": class_group,
        "second_class_group": second_class_group,
    }


async def _enroll_student(
    client: AsyncClient, headers: dict, suffix: str, class_group_id: str
) -> dict:
    student = await _post(
        client,
        "/api/v1/students",
        {
            "admission_number": f"OBHS/{suffix}/0001",
            "first_name": "Tunde",
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
    enrollment = await _post(
        client,
        f"/api/v1/students/{student['id']}/enrollments",
        {"class_group_id": class_group_id},
        headers,
    )
    return enrollment


@pytest.mark.asyncio
async def test_subjects_module_end_to_end():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, admin_token = await _create_school_and_admin(client, suffix)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        structure = await _setup_structure(client, admin_headers, suffix)
        teacher_email, teacher_password = await _create_teacher(school_id, suffix)

        # Core subject: auto-registers on enrollment.
        core_subject = await _post(
            client,
            "/api/v1/subjects",
            {"name": "General Mathematics", "code": f"MTH{suffix}"},
            admin_headers,
        )
        offering = await _post(
            client,
            "/api/v1/subjects/offerings",
            {
                "subject_id": core_subject["id"],
                "academic_session_id": structure["session"]["id"],
                "academic_level_id": structure["level"]["id"],
            },
            admin_headers,
        )
        class_subject = await _post(
            client,
            "/api/v1/class-subjects",
            {
                "subject_offering_id": offering["id"],
                "class_group_id": structure["class_group"]["id"],
            },
            admin_headers,
        )

        # The seeded Olivet 10/10/10/70 structure should exist for this class subject/term.
        definitions_resp = await client.get(
            "/api/v1/assessments/definitions",
            params={
                "academic_term_id": structure["term"]["id"],
                "class_subject_id": class_subject["id"],
            },
            headers=admin_headers,
        )
        assert definitions_resp.status_code == 200, definitions_resp.text
        definitions = definitions_resp.json()["data"]["items"]
        assert {d["code"] for d in definitions} == {"TEST1", "TEST2", "ASSIGN", "EXAM"}

        # Assign the teacher to this class subject.
        teacher_me = await client.get(
            "/api/v1/identity/me", headers=await _login(client, teacher_email, teacher_password)
        )
        teacher_id = teacher_me.json()["data"]["id"]
        assignment = await _post(
            client,
            f"/api/v1/class-subjects/{class_subject['id']}/teacher-assignments",
            {"teacher_id": teacher_id},
            admin_headers,
        )
        assert assignment["is_active"] is True

        # Cascading bulk assignment across both arms.
        bulk_resp = await client.post(
            "/api/v1/class-subjects/teacher-assignments/bulk",
            json={
                "subject_offering_id": offering["id"],
                "teacher_id": teacher_id,
                "class_group_ids": [
                    structure["class_group"]["id"],
                    structure["second_class_group"]["id"],
                ],
            },
            headers=admin_headers,
        )
        assert bulk_resp.status_code == 201, bulk_resp.text
        bulk_data = bulk_resp.json()["data"]
        # The first arm is already assigned, so only the second arm is newly assigned.
        assert len(bulk_data["assigned"]) == 1
        assert len(bulk_data["skipped"]) == 1

        # Enrolling a student auto-registers the compulsory core subject.
        enrollment = await _enroll_student(
            client, admin_headers, suffix, structure["class_group"]["id"]
        )
        regs_resp = await client.get(
            f"/api/v1/enrollments/{enrollment['id']}/subject-registrations",
            headers=admin_headers,
        )
        assert regs_resp.status_code == 200, regs_resp.text
        registrations = regs_resp.json()["data"]["items"]
        assert len(registrations) == 1
        assert registrations[0]["subject_category"] == "COMPULSORY_CORE"
        assert registrations[0]["class_subject_id"] == class_subject["id"]

        # Elective (trade) subject: never auto-registers, requires an explicit call.
        trade_subject = await _post(
            client,
            "/api/v1/subjects",
            {
                "name": "Catering Craft",
                "code": f"CCP{suffix}",
                "is_elective": True,
                "is_trade_subject": True,
            },
            admin_headers,
        )
        trade_offering = await _post(
            client,
            "/api/v1/subjects/offerings",
            {
                "subject_id": trade_subject["id"],
                "academic_session_id": structure["session"]["id"],
                "academic_level_id": structure["level"]["id"],
            },
            admin_headers,
        )
        trade_class_subject = await _post(
            client,
            "/api/v1/class-subjects",
            {
                "subject_offering_id": trade_offering["id"],
                "class_group_id": structure["class_group"]["id"],
            },
            admin_headers,
        )

        regs_after_offering = (
            await client.get(
                f"/api/v1/enrollments/{enrollment['id']}/subject-registrations",
                headers=admin_headers,
            )
        ).json()["data"]["items"]
        assert len(regs_after_offering) == 1, "trade subject must not auto-register"

        registration = await _post(
            client,
            f"/api/v1/enrollments/{enrollment['id']}/subject-registrations",
            {"class_subject_id": trade_class_subject["id"]},
            admin_headers,
        )
        assert registration["subject_category"] == "TRADE_ELECTIVE"

        # Deactivating the registration removes it from the active roster.
        delete_resp = await client.delete(
            f"/api/v1/enrollments/{enrollment['id']}/subject-registrations/{registration['id']}",
            headers=admin_headers,
        )
        assert delete_resp.status_code == 200, delete_resp.text
        regs_after_removal = (
            await client.get(
                f"/api/v1/enrollments/{enrollment['id']}/subject-registrations",
                headers=admin_headers,
            )
        ).json()["data"]["items"]
        assert len(regs_after_removal) == 1


@pytest.mark.asyncio
async def test_assessments_score_lifecycle_and_unassigned_teacher_blocked():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, admin_token = await _create_school_and_admin(client, suffix)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        structure = await _setup_structure(client, admin_headers, suffix)
        assigned_email, assigned_password = await _create_teacher(school_id, suffix, label="a")
        other_email, other_password = await _create_teacher(school_id, suffix, label="b")

        subject = await _post(
            client,
            "/api/v1/subjects",
            {"name": "General Mathematics", "code": f"MTH{suffix}"},
            admin_headers,
        )
        offering = await _post(
            client,
            "/api/v1/subjects/offerings",
            {
                "subject_id": subject["id"],
                "academic_session_id": structure["session"]["id"],
                "academic_level_id": structure["level"]["id"],
            },
            admin_headers,
        )
        class_subject = await _post(
            client,
            "/api/v1/class-subjects",
            {
                "subject_offering_id": offering["id"],
                "class_group_id": structure["class_group"]["id"],
            },
            admin_headers,
        )

        assigned_headers = await _login(client, assigned_email, assigned_password)
        assigned_teacher_id = (
            await client.get("/api/v1/identity/me", headers=assigned_headers)
        ).json()["data"]["id"]
        await _post(
            client,
            f"/api/v1/class-subjects/{class_subject['id']}/teacher-assignments",
            {"teacher_id": assigned_teacher_id},
            admin_headers,
        )

        enrollment = await _enroll_student(
            client, admin_headers, suffix, structure["class_group"]["id"]
        )

        definitions = (
            await client.get(
                "/api/v1/assessments/definitions",
                params={
                    "academic_term_id": structure["term"]["id"],
                    "class_subject_id": class_subject["id"],
                },
                headers=admin_headers,
            )
        ).json()["data"]["items"]
        assert len(definitions) == 4

        # An unassigned teacher cannot enter scores for this class subject.
        other_headers = await _login(client, other_email, other_password)
        blocked_resp = await client.post(
            "/api/v1/assessments/scores/bulk-entry",
            json={
                "class_subject_id": class_subject["id"],
                "assessment_definition_id": definitions[0]["id"],
                "scores": [
                    {
                        "student_enrollment_id": enrollment["id"],
                        "score_status": "SCORED",
                        "raw_score": 5,
                    }
                ],
            },
            headers=other_headers,
        )
        assert blocked_resp.status_code == 403, blocked_resp.text

        # The assigned teacher enters only one of the four components.
        test1 = next(d for d in definitions if d["code"] == "TEST1")
        enter_resp = await client.post(
            "/api/v1/assessments/scores/bulk-entry",
            json={
                "class_subject_id": class_subject["id"],
                "assessment_definition_id": test1["id"],
                "scores": [
                    {
                        "student_enrollment_id": enrollment["id"],
                        "score_status": "SCORED",
                        "raw_score": 8,
                    }
                ],
            },
            headers=assigned_headers,
        )
        assert enter_resp.status_code == 200, enter_resp.text
        assert enter_resp.json()["data"]["records_updated"] == 1

        # A score above the component's max_score is rejected.
        over_max_resp = await client.post(
            "/api/v1/assessments/scores/bulk-entry",
            json={
                "class_subject_id": class_subject["id"],
                "assessment_definition_id": test1["id"],
                "scores": [
                    {
                        "student_enrollment_id": enrollment["id"],
                        "score_status": "SCORED",
                        "raw_score": 12,
                    }
                ],
            },
            headers=assigned_headers,
        )
        assert over_max_resp.status_code == 200, over_max_resp.text
        assert over_max_resp.json()["data"]["records_updated"] == 0
        assert len(over_max_resp.json()["data"]["errors"]) == 1

        # Submitting while three components are still unscored is blocked.
        incomplete_submit = await client.post(
            "/api/v1/assessments/submit",
            json={
                "academic_term_id": structure["term"]["id"],
                "class_subject_id": class_subject["id"],
            },
            headers=assigned_headers,
        )
        assert incomplete_submit.status_code == 422, incomplete_submit.text

        for definition in definitions:
            if definition["code"] == "TEST1":
                continue
            await client.post(
                "/api/v1/assessments/scores/bulk-entry",
                json={
                    "class_subject_id": class_subject["id"],
                    "assessment_definition_id": definition["id"],
                    "scores": [
                        {
                            "student_enrollment_id": enrollment["id"],
                            "score_status": "SCORED",
                            "raw_score": min(5, float(definition["max_score"])),
                        }
                    ],
                },
                headers=assigned_headers,
            )

        submit_resp = await client.post(
            "/api/v1/assessments/submit",
            json={
                "academic_term_id": structure["term"]["id"],
                "class_subject_id": class_subject["id"],
            },
            headers=assigned_headers,
        )
        assert submit_resp.status_code == 200, submit_resp.text
        assert submit_resp.json()["data"]["status"] == "SUBMITTED"

        # Scores are locked once submitted.
        locked_resp = await client.post(
            "/api/v1/assessments/scores/bulk-entry",
            json={
                "class_subject_id": class_subject["id"],
                "assessment_definition_id": test1["id"],
                "scores": [
                    {
                        "student_enrollment_id": enrollment["id"],
                        "score_status": "SCORED",
                        "raw_score": 9,
                    }
                ],
            },
            headers=assigned_headers,
        )
        assert locked_resp.status_code == 422, locked_resp.text

        approve_resp = await client.post(
            "/api/v1/assessments/approve",
            json={
                "academic_term_id": structure["term"]["id"],
                "class_subject_id": class_subject["id"],
            },
            headers=admin_headers,
        )
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["data"]["status"] == "APPROVED"

        unlock_resp = await client.post(
            "/api/v1/assessments/unlock",
            json={
                "academic_term_id": structure["term"]["id"],
                "class_subject_id": class_subject["id"],
                "reason": "Arithmetic correction requested by the form master.",
            },
            headers=admin_headers,
        )
        assert unlock_resp.status_code == 200, unlock_resp.text
        assert unlock_resp.json()["data"]["status"] == "DRAFT"
        assert "correction" in unlock_resp.json()["data"]["unlock_reason"]

        reentry_resp = await client.post(
            "/api/v1/assessments/scores/bulk-entry",
            json={
                "class_subject_id": class_subject["id"],
                "assessment_definition_id": test1["id"],
                "scores": [
                    {
                        "student_enrollment_id": enrollment["id"],
                        "score_status": "SCORED",
                        "raw_score": 9,
                    }
                ],
            },
            headers=assigned_headers,
        )
        assert reentry_resp.status_code == 200, reentry_resp.text
        assert reentry_resp.json()["data"]["records_updated"] == 1


@pytest.mark.asyncio
async def test_subjects_tenant_isolation_and_permission_denial():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, admin_token = await _create_school_and_admin(client, suffix)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        structure = await _setup_structure(client, admin_headers, suffix)
        subject = await _post(
            client,
            "/api/v1/subjects",
            {"name": "General Mathematics", "code": f"MTH{suffix}"},
            admin_headers,
        )
        offering = await _post(
            client,
            "/api/v1/subjects/offerings",
            {
                "subject_id": subject["id"],
                "academic_session_id": structure["session"]["id"],
                "academic_level_id": structure["level"]["id"],
            },
            admin_headers,
        )
        class_subject = await _post(
            client,
            "/api/v1/class-subjects",
            {
                "subject_offering_id": offering["id"],
                "class_group_id": structure["class_group"]["id"],
            },
            admin_headers,
        )

        _, other_token = await _create_school_and_admin(client, f"other{suffix}")
        other_headers = {"Authorization": f"Bearer {other_token}"}

        cross_tenant_subject = await client.get(
            f"/api/v1/subjects/{subject['id']}", headers=other_headers
        )
        assert cross_tenant_subject.status_code == 404

        cross_tenant_class_subject = await client.get(
            f"/api/v1/class-subjects/{class_subject['id']}", headers=other_headers
        )
        assert cross_tenant_class_subject.status_code == 404

        definitions = (
            await client.get(
                "/api/v1/assessments/definitions",
                params={
                    "academic_term_id": structure["term"]["id"],
                    "class_subject_id": class_subject["id"],
                },
                headers=admin_headers,
            )
        ).json()["data"]["items"]
        cross_tenant_definition = await client.get(
            f"/api/v1/assessments/definitions/{definitions[0]['id']}", headers=other_headers
        )
        assert cross_tenant_definition.status_code == 404

        # A plain TEACHER has no role-default subjects:manage permission.
        teacher_email, teacher_password = await _create_teacher(school_id, suffix)
        teacher_headers = await _login(client, teacher_email, teacher_password)
        forbidden = await client.post(
            "/api/v1/subjects",
            json={"name": "Biology", "code": f"BIO{suffix}"},
            headers=teacher_headers,
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "INSUFFICIENT_PERMISSIONS"


@pytest.mark.asyncio
async def test_score_sheet_read_enforces_teacher_ownership():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, admin_token = await _create_school_and_admin(client, suffix)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        structure = await _setup_structure(client, admin_headers, suffix)
        assigned_email, assigned_password = await _create_teacher(school_id, suffix, label="a")
        other_email, other_password = await _create_teacher(school_id, suffix, label="b")

        subject = await _post(
            client,
            "/api/v1/subjects",
            {"name": "General Mathematics", "code": f"MTH{suffix}"},
            admin_headers,
        )
        offering = await _post(
            client,
            "/api/v1/subjects/offerings",
            {
                "subject_id": subject["id"],
                "academic_session_id": structure["session"]["id"],
                "academic_level_id": structure["level"]["id"],
            },
            admin_headers,
        )
        class_subject = await _post(
            client,
            "/api/v1/class-subjects",
            {
                "subject_offering_id": offering["id"],
                "class_group_id": structure["class_group"]["id"],
            },
            admin_headers,
        )
        definitions = (
            await client.get(
                "/api/v1/assessments/definitions",
                params={
                    "academic_term_id": structure["term"]["id"],
                    "class_subject_id": class_subject["id"],
                },
                headers=admin_headers,
            )
        ).json()["data"]["items"]

        assigned_headers = await _login(client, assigned_email, assigned_password)
        assigned_teacher_id = (
            await client.get("/api/v1/identity/me", headers=assigned_headers)
        ).json()["data"]["id"]
        await _post(
            client,
            f"/api/v1/class-subjects/{class_subject['id']}/teacher-assignments",
            {"teacher_id": assigned_teacher_id},
            admin_headers,
        )

        # The assigned teacher can read the sheet.
        allowed_resp = await client.get(
            "/api/v1/assessments/scores",
            params={
                "class_subject_id": class_subject["id"],
                "assessment_definition_id": definitions[0]["id"],
            },
            headers=assigned_headers,
        )
        assert allowed_resp.status_code == 200, allowed_resp.text

        # An unassigned teacher cannot read the same sheet.
        other_headers = await _login(client, other_email, other_password)
        blocked_resp = await client.get(
            "/api/v1/assessments/scores",
            params={
                "class_subject_id": class_subject["id"],
                "assessment_definition_id": definitions[0]["id"],
            },
            headers=other_headers,
        )
        assert blocked_resp.status_code == 403, blocked_resp.text
        assert blocked_resp.json()["error"]["code"] == "INSUFFICIENT_PERMISSIONS"


@pytest.mark.asyncio
async def test_department_scoped_delegate_can_approve_scores():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, admin_token = await _create_school_and_admin(client, suffix)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        session = await _post(
            client,
            "/api/v1/structure/sessions",
            {
                "name": f"2024/2025 D{suffix}",
                "start_date": "2024-09-09",
                "end_date": "2025-07-18",
                "is_current": True,
            },
            admin_headers,
        )
        term = await _post(
            client,
            f"/api/v1/structure/sessions/{session['id']}/terms",
            {
                "name": "First Term",
                "term_order": 1,
                "start_date": "2024-09-09",
                "end_date": "2024-12-13",
                "is_current": True,
            },
            admin_headers,
        )
        level = await _post(
            client,
            "/api/v1/structure/levels",
            {
                "name": "SS 1",
                "code": f"SS1{suffix}",
                "level_order": 4,
                "category": "SENIOR_SECONDARY",
            },
            admin_headers,
        )
        department = await _post(
            client,
            "/api/v1/structure/departments",
            {"name": "Science", "code": f"SCI{suffix}"},
            admin_headers,
        )
        arm = await _post(client, "/api/v1/structure/arms", {"name": f"O{suffix}"}, admin_headers)
        class_group = await _post(
            client,
            "/api/v1/structure/class-groups",
            {
                "academic_session_id": session["id"],
                "academic_level_id": level["id"],
                "department_id": department["id"],
                "class_arm_id": arm["id"],
            },
            admin_headers,
        )

        subject = await _post(
            client,
            "/api/v1/subjects",
            {"name": "Chemistry", "code": f"CHM{suffix}"},
            admin_headers,
        )
        offering = await _post(
            client,
            "/api/v1/subjects/offerings",
            {
                "subject_id": subject["id"],
                "academic_session_id": session["id"],
                "academic_level_id": level["id"],
                "department_id": department["id"],
            },
            admin_headers,
        )
        class_subject = await _post(
            client,
            "/api/v1/class-subjects",
            {"subject_offering_id": offering["id"], "class_group_id": class_group["id"]},
            admin_headers,
        )

        teacher_email, teacher_password = await _create_teacher(school_id, suffix, label="chem")
        teacher_headers = await _login(client, teacher_email, teacher_password)
        teacher_id = (await client.get("/api/v1/identity/me", headers=teacher_headers)).json()[
            "data"
        ]["id"]
        await _post(
            client,
            f"/api/v1/class-subjects/{class_subject['id']}/teacher-assignments",
            {"teacher_id": teacher_id},
            admin_headers,
        )

        enrollment = await _enroll_student(client, admin_headers, suffix, class_group["id"])

        # Register a department-scoped HOD: a plain TEACHER role with a delegated,
        # DEPARTMENT-scoped scores:approve grant -- no school-wide or role-default access.
        hod_email, hod_password = await _create_teacher(school_id, suffix, label="hod")
        hod_headers = await _login(client, hod_email, hod_password)
        hod_id = (await client.get("/api/v1/identity/me", headers=hod_headers)).json()["data"]["id"]
        grant_resp = await client.post(
            f"/api/v1/identity/users/{hod_id}/permissions",
            json={
                "permission_code": "scores:approve",
                "scope_type": "DEPARTMENT",
                "department_id": department["id"],
            },
            headers=admin_headers,
        )
        assert grant_resp.status_code == 201, grant_resp.text

        definitions = (
            await client.get(
                "/api/v1/assessments/definitions",
                params={"academic_term_id": term["id"], "class_subject_id": class_subject["id"]},
                headers=admin_headers,
            )
        ).json()["data"]["items"]
        for definition in definitions:
            await client.post(
                "/api/v1/assessments/scores/bulk-entry",
                json={
                    "class_subject_id": class_subject["id"],
                    "assessment_definition_id": definition["id"],
                    "scores": [
                        {
                            "student_enrollment_id": enrollment["id"],
                            "score_status": "SCORED",
                            "raw_score": min(5, float(definition["max_score"])),
                        }
                    ],
                },
                headers=teacher_headers,
            )
        submit_resp = await client.post(
            "/api/v1/assessments/submit",
            json={"academic_term_id": term["id"], "class_subject_id": class_subject["id"]},
            headers=teacher_headers,
        )
        assert submit_resp.status_code == 200, submit_resp.text

        # The department-scoped HOD -- not school-wide, not role-default, not the
        # assigned teacher -- can approve scores for this Science class subject.
        approve_resp = await client.post(
            "/api/v1/assessments/approve",
            json={"academic_term_id": term["id"], "class_subject_id": class_subject["id"]},
            headers=hod_headers,
        )
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["data"]["status"] == "APPROVED"


async def _grant(
    client: AsyncClient, admin_headers: dict, user_id: str, code: str, department_id: str
) -> None:
    resp = await client.post(
        f"/api/v1/identity/users/{user_id}/permissions",
        json={"permission_code": code, "scope_type": "DEPARTMENT", "department_id": department_id},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_department_scoped_delegate_can_manage_structure_subjects_and_enrollment():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, admin_token = await _create_school_and_admin(client, suffix)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        session = await _post(
            client,
            "/api/v1/structure/sessions",
            {
                "name": f"2024/2025 D{suffix}",
                "start_date": "2024-09-09",
                "end_date": "2025-07-18",
                "is_current": True,
            },
            admin_headers,
        )
        await _post(
            client,
            f"/api/v1/structure/sessions/{session['id']}/terms",
            {
                "name": "First Term",
                "term_order": 1,
                "start_date": "2024-09-09",
                "end_date": "2024-12-13",
                "is_current": True,
            },
            admin_headers,
        )
        level = await _post(
            client,
            "/api/v1/structure/levels",
            {
                "name": "SS 1",
                "code": f"SS1{suffix}",
                "level_order": 4,
                "category": "SENIOR_SECONDARY",
            },
            admin_headers,
        )
        science = await _post(
            client,
            "/api/v1/structure/departments",
            {"name": "Science", "code": f"SCI{suffix}"},
            admin_headers,
        )
        arts = await _post(
            client,
            "/api/v1/structure/departments",
            {"name": "Arts", "code": f"ART{suffix}"},
            admin_headers,
        )
        arm = await _post(client, "/api/v1/structure/arms", {"name": f"O{suffix}"}, admin_headers)

        delegate_email, delegate_password = await _create_teacher(school_id, suffix, label="deleg")
        delegate_headers = await _login(client, delegate_email, delegate_password)
        delegate_id = (await client.get("/api/v1/identity/me", headers=delegate_headers)).json()[
            "data"
        ]["id"]
        for code in ("structure:manage", "subjects:manage", "students:enroll"):
            await _grant(client, admin_headers, delegate_id, code, science["id"])

        science_group = await _post(
            client,
            "/api/v1/structure/class-groups",
            {
                "academic_session_id": session["id"],
                "academic_level_id": level["id"],
                "department_id": science["id"],
                "class_arm_id": arm["id"],
            },
            delegate_headers,
        )

        forbidden_group = await client.post(
            "/api/v1/structure/class-groups",
            json={
                "academic_session_id": session["id"],
                "academic_level_id": level["id"],
                "department_id": arts["id"],
                "class_arm_id": arm["id"],
            },
            headers=delegate_headers,
        )
        assert forbidden_group.status_code == 403, forbidden_group.text

        # The admin (unscoped) creates an Arts class group the delegate has no grant for.
        arts_group = await _post(
            client,
            "/api/v1/structure/class-groups",
            {
                "academic_session_id": session["id"],
                "academic_level_id": level["id"],
                "department_id": arts["id"],
                "class_arm_id": arm["id"],
            },
            admin_headers,
        )

        # Listing as the delegate returns only the Science group, not Arts --
        # a filtered view, not a 403 and not the whole school's class groups.
        delegate_list = await client.get("/api/v1/structure/class-groups", headers=delegate_headers)
        assert delegate_list.status_code == 200, delegate_list.text
        delegate_group_ids = {g["id"] for g in delegate_list.json()["data"]["items"]}
        assert science_group["id"] in delegate_group_ids
        assert arts_group["id"] not in delegate_group_ids

        # The admin's unscoped list still sees both.
        admin_list = await client.get("/api/v1/structure/class-groups", headers=admin_headers)
        admin_group_ids = {g["id"] for g in admin_list.json()["data"]["items"]}
        assert science_group["id"] in admin_group_ids
        assert arts_group["id"] in admin_group_ids

        subject = await _post(
            client,
            "/api/v1/subjects",
            {"name": "Physics", "code": f"PHY{suffix}"},
            admin_headers,
        )
        offering = await _post(
            client,
            "/api/v1/subjects/offerings",
            {
                "subject_id": subject["id"],
                "academic_session_id": session["id"],
                "academic_level_id": level["id"],
                "department_id": science["id"],
            },
            admin_headers,
        )

        class_subject = await _post(
            client,
            "/api/v1/class-subjects",
            {"subject_offering_id": offering["id"], "class_group_id": science_group["id"]},
            delegate_headers,
        )
        assert class_subject["class_group_id"] == science_group["id"]

        student = await _post(
            client,
            "/api/v1/students",
            {
                "admission_number": f"OBHS/{suffix}/0002",
                "first_name": "Ngozi",
                "last_name": "Eze",
                "date_of_birth": "2010-01-15",
                "gender": "FEMALE",
                "guardian_name": "Mrs Eze",
                "guardian_email": f"guardian2.{suffix}@olivet-test.edu.ng",
                "guardian_phone": "08039876543",
                "enrollment_date": "2024-09-09",
            },
            admin_headers,
        )

        enrollment = await _post(
            client,
            f"/api/v1/students/{student['id']}/enrollments",
            {"class_group_id": science_group["id"]},
            delegate_headers,
        )
        assert enrollment["class_group_id"] == science_group["id"]

        # A plain teacher with no role default and no grant at all is denied
        # outright on the list endpoint, not given an empty-but-successful list.
        bystander_email, bystander_password = await _create_teacher(school_id, suffix, label="by")
        bystander_headers = await _login(client, bystander_email, bystander_password)
        bystander_list = await client.get(
            "/api/v1/structure/class-groups", headers=bystander_headers
        )
        assert bystander_list.status_code == 403, bystander_list.text


@pytest.mark.asyncio
async def test_calculate_term_broadsheet_and_publish_term_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, admin_token = await _create_school_and_admin(client, suffix)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        structure = await _setup_structure(client, admin_headers, suffix)
        term_id = structure["term"]["id"]
        class_group_id = structure["class_group"]["id"]

        subject = await _post(
            client,
            "/api/v1/subjects",
            {"name": "General Mathematics", "code": f"MTH{suffix}"},
            admin_headers,
        )
        offering = await _post(
            client,
            "/api/v1/subjects/offerings",
            {
                "subject_id": subject["id"],
                "academic_session_id": structure["session"]["id"],
                "academic_level_id": structure["level"]["id"],
            },
            admin_headers,
        )
        class_subject = await _post(
            client,
            "/api/v1/class-subjects",
            {"subject_offering_id": offering["id"], "class_group_id": class_group_id},
            admin_headers,
        )
        enrollment = await _enroll_student(client, admin_headers, suffix, class_group_id)

        definitions = (
            await client.get(
                "/api/v1/assessments/definitions",
                params={"academic_term_id": term_id, "class_subject_id": class_subject["id"]},
                headers=admin_headers,
            )
        ).json()["data"]["items"]

        # Broadsheet preview works even when all/some scores are still PENDING and unapproved.
        pending_preview_resp = await client.get(
            "/api/v1/computation/broadsheet",
            params={"academic_term_id": term_id},
            headers=admin_headers,
        )
        assert pending_preview_resp.status_code == 200, pending_preview_resp.text
        assert pending_preview_resp.json()["data"]["total_students"] == 1

        for definition in definitions:
            score_resp = await client.post(
                "/api/v1/assessments/scores/bulk-entry",
                json={
                    "class_subject_id": class_subject["id"],
                    "assessment_definition_id": definition["id"],
                    "scores": [
                        {
                            "student_enrollment_id": enrollment["id"],
                            "score_status": "SCORED",
                            "raw_score": min(5, float(definition["max_score"])),
                        }
                    ],
                },
                headers=admin_headers,
            )
            assert score_resp.status_code == 200, score_resp.text

        # Broadsheet preview works even before score sheets are submitted/approved or calculated.
        preview_resp = await client.get(
            f"/api/v1/computation/broadsheet/{class_group_id}",
            params={"academic_term_id": term_id},
            headers=admin_headers,
        )
        assert preview_resp.status_code == 200, preview_resp.text
        assert preview_resp.json()["data"]["total_students"] == 1

        filtered_preview_resp = await client.get(
            "/api/v1/computation/broadsheet",
            params={"academic_term_id": term_id},
            headers=admin_headers,
        )
        assert filtered_preview_resp.status_code == 200, filtered_preview_resp.text
        assert filtered_preview_resp.json()["data"]["total_students"] == 1

        # Official calculation still requires approved score sheets.
        unapproved_calc_resp = await client.post(
            "/api/v1/computation/calculate-term",
            json={"academic_term_id": term_id, "class_group_id": class_group_id},
            headers=admin_headers,
        )
        assert unapproved_calc_resp.status_code == 422, unapproved_calc_resp.text

        submit_resp = await client.post(
            "/api/v1/assessments/submit",
            json={"academic_term_id": term_id, "class_subject_id": class_subject["id"]},
            headers=admin_headers,
        )
        assert submit_resp.status_code == 200, submit_resp.text
        approve_resp = await client.post(
            "/api/v1/assessments/approve",
            json={"academic_term_id": term_id, "class_subject_id": class_subject["id"]},
            headers=admin_headers,
        )
        assert approve_resp.status_code == 200, approve_resp.text

        # Regression for the UUID-shadowing bug: this used to 500 on every call.
        calc_resp = await client.post(
            "/api/v1/computation/calculate-term",
            json={"academic_term_id": term_id, "class_group_id": class_group_id},
            headers=admin_headers,
        )
        assert calc_resp.status_code == 200, calc_resp.text
        calc_data = calc_resp.json()["data"]
        assert calc_data["students_calculated"] == 1
        assert calc_data["subject_results_upserted"] == 1

        broadsheet_resp = await client.get(
            f"/api/v1/computation/broadsheet/{class_group_id}",
            params={"academic_term_id": term_id},
            headers=admin_headers,
        )
        assert broadsheet_resp.status_code == 200, broadsheet_resp.text
        broadsheet_data = broadsheet_resp.json()["data"]
        assert broadsheet_data["total_students"] == 1
        # First term is not an annual term, so no cumulative data is attached yet.
        assert broadsheet_data["students"][0]["annual_score"] is None
        assert broadsheet_data["students"][0]["promotion_decision"] is None

        publish_resp = await client.post(
            "/api/v1/publication/publish-term",
            json={"academic_term_id": term_id},
            headers=admin_headers,
        )
        assert publish_resp.status_code == 202, publish_resp.text
        publish_data = publish_resp.json()["data"]
        assert publish_data["report_cards_published"] == 1
        assert len(publish_data["result_links"]) == 1

        # Regression for the permanent-lock bug: any other change while locked is
        # still rejected, but an unlock-only request must succeed.
        blocked_edit = await client.patch(
            f"/api/v1/structure/terms/{term_id}",
            json={"name": "First Term Renamed"},
            headers=admin_headers,
        )
        assert blocked_edit.status_code == 422, blocked_edit.text

        unlock_term_resp = await client.patch(
            f"/api/v1/structure/terms/{term_id}",
            json={"is_locked_for_scoring": False},
            headers=admin_headers,
        )
        assert unlock_term_resp.status_code == 200, unlock_term_resp.text
        assert unlock_term_resp.json()["data"]["is_locked_for_scoring"] is False

        # Published results stay frozen even once the term itself is unlocked.
        recalc_after_unlock = await client.post(
            "/api/v1/computation/calculate-term",
            json={"academic_term_id": term_id, "class_group_id": class_group_id},
            headers=admin_headers,
        )
        assert recalc_after_unlock.status_code == 409, recalc_after_unlock.text
