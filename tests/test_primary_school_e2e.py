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


async def _create_primary_school_and_admin(
    client: AsyncClient, suffix: str
) -> tuple[str, str, dict]:
    payload = {
        "name": f"Olivet Primary E2E School {suffix}",
        "slug": f"olivet-primary-e2e-{suffix}",
        "official_code": f"OBHS-PRM-{suffix.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": "Olivet Heights, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": f"primary.admin.{suffix}@olivet-test.edu.ng",
        "admin_first_name": "Adebayo",
        "admin_last_name": "Adeyemi",
        "admin_password": "PrimaryAdminSecurePassword123!",
        "school_type": "PRIMARY",
    }
    response = await client.post("/api/v1/identity/schools", json=payload)
    assert response.status_code == 201, response.text
    school_id = response.json()["data"]["id"]

    login = await client.post(
        "/api/v1/identity/login",
        json={"email": payload["admin_email"], "password": payload["admin_password"]},
    )
    assert login.status_code == 200, login.text
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return school_id, token, headers


async def _enroll_student(
    client: AsyncClient, headers: dict, suffix: str, class_group_id: str
) -> dict:
    student_resp = await client.post(
        "/api/v1/students",
        json={
            "first_name": "Kehinde",
            "last_name": f"Ogunleye_{suffix}",
            "admission_number": f"PRI/{suffix}/0001",
            "gender": "MALE",
            "date_of_birth": "2018-05-12",
            "guardian_name": "Mr Ogunleye",
            "guardian_email": f"guardian.{suffix}@olivet-test.edu.ng",
            "guardian_phone": "08031234567",
            "enrollment_date": "2025-09-08",
        },
        headers=headers,
    )
    assert student_resp.status_code == 201, student_resp.text
    student_id = student_resp.json()["data"]["id"]

    enrollment_resp = await client.post(
        f"/api/v1/students/{student_id}/enrollments",
        json={"class_group_id": class_group_id},
        headers=headers,
    )
    assert enrollment_resp.status_code == 201, enrollment_resp.text
    return enrollment_resp.json()["data"]


@pytest.mark.asyncio
async def test_primary_school_e2e_full_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, token, headers = await _create_primary_school_and_admin(client, suffix)

        # 1. Verify school setup configuration defaults
        setup_resp = await client.get(
            f"/api/v1/identity/schools/{school_id}/setup", headers=headers
        )
        assert setup_resp.status_code == 200, setup_resp.text
        setup_data = setup_resp.json()["data"]
        assert setup_data["school"]["school_type"] == "PRIMARY"
        config = setup_data["configuration"]
        assert config["report_card_template_id"] == "default_primary_v1"
        assert [g["grade"] for g in config["grading_scale_schema"]["grades"]] == [
            "A",
            "B",
            "C",
            "D",
            "E",
            "F",
        ]
        assert set(config["assessment_weight_schema"].keys()) == {
            "TEST1",
            "TEST2",
            "ASSIGN",
            "EXAM",
        }
        assert config["assessment_weight_schema"]["EXAM"]["weight_percentage"] == 70

        # 2. Create academic session and First Term first
        sess_resp = await client.post(
            "/api/v1/structure/sessions",
            json={
                "name": f"2025/2026 {suffix}",
                "start_date": "2025-09-08",
                "end_date": "2026-07-20",
                "is_current": True,
            },
            headers=headers,
        )
        assert sess_resp.status_code == 201, sess_resp.text
        session_id = sess_resp.json()["data"]["id"]

        term_resp = await client.post(
            f"/api/v1/structure/sessions/{session_id}/terms",
            json={
                "name": "First Term",
                "term_order": 1,
                "start_date": "2025-09-08",
                "end_date": "2025-12-19",
                "is_current": True,
            },
            headers=headers,
        )
        assert term_resp.status_code == 201, term_resp.text
        term_id = term_resp.json()["data"]["id"]

        # 3. Apply the primary template nigeria_primary_basic1_6 for this session
        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_id": "nigeria_primary_basic1_6",
                "template_type": "PRIMARY",
                "academic_session_id": session_id,
                "include_class_groups": True,
            },
            headers=headers,
        )
        assert apply_resp.status_code == 201, apply_resp.text
        tpl_data = apply_resp.json()["data"]
        assert tpl_data["levels_created"] == 6
        assert tpl_data["departments_created"] == 0
        assert tpl_data["arms_created"] == 4
        assert tpl_data["class_groups_created"] == 24
        assert tpl_data["subjects_created"] == 10
        assert tpl_data["subject_offerings_created"] == 60
        assert tpl_data["class_subjects_created"] == 240

        # Fetch class groups to find Basic 1 A
        levels_resp = await client.get("/api/v1/structure/levels", headers=headers)
        basic1_level = next(
            lvl for lvl in levels_resp.json()["data"]["items"] if lvl["code"] == "BASIC1"
        )
        basic_1_level_id = basic1_level["id"]

        cg_resp = await client.get(
            "/api/v1/structure/class-groups",
            params={"session_id": session_id, "page_size": 100},
            headers=headers,
        )
        assert cg_resp.status_code == 200, cg_resp.text
        all_cgs = cg_resp.json()["data"]["items"]
        basic_1_a = next(
            cg
            for cg in all_cgs
            if cg["academic_level_id"] == basic_1_level_id and "A" in cg["display_name"]
        )

        # 3. Enroll a student in Basic 1-A
        enrollment = await _enroll_student(client, headers, suffix, basic_1_a["id"])

        # 4. Verify automatic compulsory subject registration for all 10 primary subjects
        reg_resp = await client.get(
            f"/api/v1/enrollments/{enrollment['id']}/subject-registrations",
            headers=headers,
        )
        assert reg_resp.status_code == 200, reg_resp.text
        registrations = reg_resp.json()["data"]["items"]
        assert len(registrations) == 10
        assert all(reg["subject_category"] == "COMPULSORY_CORE" for reg in registrations)
        assert all(reg["is_active"] is True for reg in registrations)

        # 5. Verify that attempting to pass department_id to a primary level/class group/offering raises ValidationException
        dept_resp = await client.post(
            "/api/v1/structure/departments",
            json={"name": "Sciences", "code": f"SCI{suffix}"},
            headers=headers,
        )
        assert dept_resp.status_code == 201, dept_resp.text
        dept_id = dept_resp.json()["data"]["id"]

        # 5a. Attempt to create class group with department_id on primary level
        arm_id = basic_1_a["class_arm_id"]
        invalid_cg_resp = await client.post(
            "/api/v1/structure/class-groups",
            json={
                "academic_session_id": session_id,
                "academic_level_id": basic_1_level_id,
                "department_id": dept_id,
                "class_arm_id": arm_id,
            },
            headers=headers,
        )
        assert invalid_cg_resp.status_code == 422, invalid_cg_resp.text
        assert (
            "Primary classes do not use a department" in invalid_cg_resp.json()["error"]["message"]
        )

        # 5b. Attempt to create subject offering with department_id on primary level
        subj_resp = await client.get("/api/v1/subjects", params={"page_size": 10}, headers=headers)
        first_subject = subj_resp.json()["data"]["items"][0]
        invalid_offering_resp = await client.post(
            "/api/v1/subjects/offerings",
            json={
                "subject_id": first_subject["id"],
                "academic_session_id": session_id,
                "academic_level_id": basic_1_level_id,
                "department_id": dept_id,
            },
            headers=headers,
        )
        assert invalid_offering_resp.status_code == 422, invalid_offering_resp.text
        assert (
            "Primary subject offerings do not use a department"
            in invalid_offering_resp.json()["error"]["message"]
        )

        # 6. Test score entry, submission, approval, computation, and broadsheet
        cs_resp = await client.get(
            "/api/v1/class-subjects",
            params={"class_group_id": basic_1_a["id"], "page_size": 20},
            headers=headers,
        )
        assert cs_resp.status_code == 200, cs_resp.text
        class_subjects = cs_resp.json()["data"]["items"]
        assert len(class_subjects) == 10

        # Enter scores, submit, and approve all 10 primary class subjects
        scores_map = {"TEST1": 8, "TEST2": 9, "ASSIGN": 8, "EXAM": 55}  # Total = 80 -> Grade A
        for cs in class_subjects:
            defs_resp = await client.get(
                "/api/v1/assessments/definitions",
                params={"academic_term_id": term_id, "class_subject_id": cs["id"]},
                headers=headers,
            )
            assert defs_resp.status_code == 200, defs_resp.text
            definitions = {d["code"]: d for d in defs_resp.json()["data"]["items"]}
            assert set(definitions.keys()) == {"TEST1", "TEST2", "ASSIGN", "EXAM"}

            for code, raw_score in scores_map.items():
                defn = definitions[code]
                score_entry_resp = await client.post(
                    "/api/v1/assessments/scores/bulk-entry",
                    json={
                        "class_subject_id": cs["id"],
                        "assessment_definition_id": defn["id"],
                        "scores": [
                            {
                                "student_enrollment_id": enrollment["id"],
                                "score_status": "SCORED",
                                "raw_score": raw_score,
                            }
                        ],
                    },
                    headers=headers,
                )
                assert score_entry_resp.status_code == 200, score_entry_resp.text

            submit_resp = await client.post(
                "/api/v1/assessments/submit",
                json={"academic_term_id": term_id, "class_subject_id": cs["id"]},
                headers=headers,
            )
            assert submit_resp.status_code == 200, submit_resp.text

            approve_resp = await client.post(
                "/api/v1/assessments/approve",
                json={"academic_term_id": term_id, "class_subject_id": cs["id"]},
                headers=headers,
            )
            assert approve_resp.status_code == 200, approve_resp.text

        # Compute term results
        calc_resp = await client.post(
            "/api/v1/computation/calculate-term",
            json={"academic_term_id": term_id, "class_group_id": basic_1_a["id"]},
            headers=headers,
        )
        assert calc_resp.status_code == 200, calc_resp.text
        calc_data = calc_resp.json()["data"]
        assert calc_data["students_calculated"] == 1
        assert calc_data["subject_results_upserted"] == 10

        # Check broadsheet
        bs_resp = await client.get(
            f"/api/v1/computation/broadsheet/{basic_1_a['id']}",
            params={"academic_term_id": term_id},
            headers=headers,
        )
        assert bs_resp.status_code == 200, bs_resp.text
        bs_data = bs_resp.json()["data"]
        student_bs = bs_data["students"][0]
        subjects_by_id = {s["class_subject_id"]: s for s in student_bs["subjects"]}
        # In primary grading scale (A: 70-100), 80/100 must yield grade "A", NOT secondary "A1"
        for cs in class_subjects:
            subj_result = subjects_by_id[cs["id"]]
            assert subj_result["total_score"] == "80.00"
            assert subj_result["grade"] == "A"
            assert subj_result["remark"] == "Excellent"

        # Publish term & check report card
        pub_resp = await client.post(
            "/api/v1/publication/publish-term",
            json={"academic_term_id": term_id},
            headers=headers,
        )
        assert pub_resp.status_code == 202, pub_resp.text
        pub_data = pub_resp.json()["data"]
        assert pub_data["report_cards_published"] == 1


@pytest.mark.asyncio
async def test_primary_compulsory_registration_backfill():
    """Verify that when a student is already enrolled in a class before template
    subjects are applied, applying the primary template backfills all 10 compulsory
    subject registrations for that student."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, token, headers = await _create_primary_school_and_admin(client, suffix)

        # 1. Manually create a session, level, arm, and class group without subjects
        sess_resp = await client.post(
            "/api/v1/structure/sessions",
            json={
                "name": f"2025/2026 {suffix}",
                "start_date": "2025-09-08",
                "end_date": "2026-07-20",
            },
            headers=headers,
        )
        assert sess_resp.status_code == 201, sess_resp.text
        session_id = sess_resp.json()["data"]["id"]

        lvl_resp = await client.post(
            "/api/v1/structure/levels",
            json={"name": "Basic 1", "code": "BASIC1", "level_order": 1, "category": "PRIMARY"},
            headers=headers,
        )
        assert lvl_resp.status_code == 201, lvl_resp.text
        level_id = lvl_resp.json()["data"]["id"]

        arm_resp = await client.post(
            "/api/v1/structure/arms",
            json={"name": "A"},
            headers=headers,
        )
        assert arm_resp.status_code == 201, arm_resp.text
        arm_id = arm_resp.json()["data"]["id"]

        cg_resp = await client.post(
            "/api/v1/structure/class-groups",
            json={
                "academic_session_id": session_id,
                "academic_level_id": level_id,
                "class_arm_id": arm_id,
            },
            headers=headers,
        )
        assert cg_resp.status_code == 201, cg_resp.text
        cg_id = cg_resp.json()["data"]["id"]

        # 2. Enroll student into this class group (currently 0 subjects exist)
        enrollment = await _enroll_student(client, headers, suffix, cg_id)

        # Verify student currently has 0 registrations
        reg_before = await client.get(
            f"/api/v1/enrollments/{enrollment['id']}/subject-registrations",
            headers=headers,
        )
        assert reg_before.status_code == 200, reg_before.text
        assert len(reg_before.json()["data"]["items"]) == 0

        # 3. Apply the primary template for this existing session
        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_type": "PRIMARY",
                "academic_session_id": session_id,
                "include_class_groups": True,
            },
            headers=headers,
        )
        assert apply_resp.status_code == 201, apply_resp.text
        apply_data = apply_resp.json()["data"]
        # Backfill should have created subject registrations for the enrolled student!
        assert apply_data["subject_registrations_created"] == 10

        # 4. Verify student now has all 10 compulsory registrations
        reg_after = await client.get(
            f"/api/v1/enrollments/{enrollment['id']}/subject-registrations",
            headers=headers,
        )
        assert reg_after.status_code == 200, reg_after.text
        items = reg_after.json()["data"]["items"]
        assert len(items) == 10
        assert all(item["subject_category"] == "COMPULSORY_CORE" for item in items)

        # 5. Create a term AFTER template application and verify assessment definitions are seeded
        term_resp = await client.post(
            f"/api/v1/structure/sessions/{session_id}/terms",
            json={
                "name": "First Term",
                "term_order": 1,
                "start_date": "2025-09-15",
                "end_date": "2025-12-15",
                "is_current": True,
            },
            headers=headers,
        )
        assert term_resp.status_code == 201, term_resp.text
        term_id = term_resp.json()["data"]["id"]

        cs_id = items[0]["class_subject_id"]
        defs_resp = await client.get(
            f"/api/v1/assessments/definitions?academic_term_id={term_id}&class_subject_id={cs_id}",
            headers=headers,
        )
        assert defs_resp.status_code == 200, defs_resp.text
        defs = defs_resp.json()["data"]["items"]
        assert {d["code"] for d in defs} == {"TEST1", "TEST2", "ASSIGN", "EXAM"}
