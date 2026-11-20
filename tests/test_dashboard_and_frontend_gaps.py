import uuid
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import async_session_factory
from app.main import app
from app.modules.identity.seed import seed_system_permissions


@pytest.fixture
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


async def _register_and_login(client: AsyncClient, suffix: str) -> tuple[str, dict[str, str], str]:
    email = f"gap.admin.{suffix}@olivet-test.edu.ng"
    password = "StrongAdminPassword123!"
    resp = await client.post(
        "/api/v1/identity/schools",
        json={
            "name": f"Olivet Gap School {suffix}",
            "slug": f"olivet-gap-{suffix}",
            "official_code": f"GAP-{suffix.upper()}",
            "state": "Oyo",
            "city": "Oyo",
            "address": "10 Academy Road",
            "country_code": "NG",
            "admin_email": email,
            "admin_first_name": "Tunde",
            "admin_last_name": "Balogun",
            "admin_password": password,
            "school_type": "PRIMARY",
        },
    )
    assert resp.status_code == 201, resp.text
    school_id = resp.json()["data"]["id"]

    login = await client.post(
        "/api/v1/identity/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    token = login.json()["data"]["access_token"]
    return school_id, {"Authorization": f"Bearer {token}"}, email


@pytest.mark.asyncio
async def test_dashboard_staff_filters_attendance_sheets_and_report_cards(
    seed_permissions_fixture,
):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        _, headers, email = await _register_and_login(client, suffix)

        me = await client.get("/api/v1/identity/me", headers=headers)
        assert me.status_code == 200, me.text
        user_id = me.json()["data"]["id"]

        # Update profile and change password, then verify login with new password
        patch_me = await client.patch(
            "/api/v1/identity/me",
            json={"first_name": "TundeUpdated", "phone_number": "+2348099990000"},
            headers=headers,
        )
        assert patch_me.status_code == 200, patch_me.text
        assert patch_me.json()["data"]["first_name"] == "TundeUpdated"

        new_password = "NewStrongPassword456!"
        chg_pw = await client.post(
            "/api/v1/identity/change-password",
            json={"current_password": "StrongAdminPassword123!", "new_password": new_password},
            headers=headers,
        )
        assert chg_pw.status_code == 200, chg_pw.text
        relogin = await client.post(
            "/api/v1/identity/login",
            json={"email": email, "password": new_password},
        )
        assert relogin.status_code == 200, relogin.text

        # Staff directory, permissions catalog, and user grants
        users_resp = await client.get("/api/v1/identity/users", headers=headers)
        assert users_resp.status_code == 200, users_resp.text
        assert users_resp.json()["data"]["summary"]["total"] == 1

        perms_resp = await client.get("/api/v1/identity/permissions", headers=headers)
        assert perms_resp.status_code == 200, perms_resp.text
        assert len(perms_resp.json()["data"]) > 0

        grants_resp = await client.get(
            f"/api/v1/identity/users/{user_id}/permissions", headers=headers
        )
        assert grants_resp.status_code == 200, grants_resp.text
        assert grants_resp.json()["data"] == []

        # Apply PRIMARY template + create term
        today = date.today()
        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_type": "PRIMARY",
                "session_name": f"2026/2027 {suffix}",
                "session_start_date": (today - timedelta(days=30)).isoformat(),
                "session_end_date": (today + timedelta(days=60)).isoformat(),
                "include_class_groups": True,
            },
            headers=headers,
        )
        assert apply_resp.status_code == 201, apply_resp.text
        session_id = apply_resp.json()["data"]["academic_session_id"]

        term_resp = await client.post(
            f"/api/v1/structure/sessions/{session_id}/terms",
            json={
                "name": "First Term",
                "term_order": 1,
                "start_date": (today - timedelta(days=30)).isoformat(),
                "end_date": (today + timedelta(days=30)).isoformat(),
                "is_current": True,
            },
            headers=headers,
        )
        assert term_resp.status_code == 201, term_resp.text
        term_id = term_resp.json()["data"]["id"]

        # Unassigned class groups filter
        unassigned_cgs = await client.get(
            f"/api/v1/structure/class-groups?session_id={session_id}&has_class_teacher=false&page_size=50",
            headers=headers,
        )
        assert unassigned_cgs.status_code == 200, unassigned_cgs.text
        assert unassigned_cgs.json()["data"]["meta"]["total"] > 0
        basic1_a = next(
            g for g in unassigned_cgs.json()["data"]["items"] if g["display_name"] == "Basic 1 A"
        )
        cg_id = basic1_a["id"]

        # Assign class teacher and verify enriched fields
        await client.patch(
            f"/api/v1/structure/class-groups/{cg_id}",
            json={"class_teacher_id": user_id},
            headers=headers,
        )
        assigned_cgs = await client.get(
            f"/api/v1/structure/class-groups?session_id={session_id}&has_class_teacher=true",
            headers=headers,
        )
        assert assigned_cgs.status_code == 200, assigned_cgs.text
        assert (
            assigned_cgs.json()["data"]["items"][0]["class_teacher_name"] == "TundeUpdated Balogun"
        )

        # Create & enroll student, then check /students/summary and /students filters
        stu_resp = await client.post(
            "/api/v1/students",
            json={
                "admission_number": f"GAP-{suffix}-001",
                "first_name": "Chinedu",
                "last_name": "Okafor",
                "date_of_birth": "2018-05-10",
                "gender": "MALE",
                "guardian_name": "Ada Okafor",
                "guardian_email": f"ada.{suffix}@example.com",
                "guardian_phone": "+2348012345678",
                "enrollment_date": (today - timedelta(days=20)).isoformat(),
            },
            headers=headers,
        )
        assert stu_resp.status_code == 201, stu_resp.text
        student_id = stu_resp.json()["data"]["id"]

        enroll_resp = await client.post(
            f"/api/v1/students/{student_id}/enrollments",
            json={"class_group_id": cg_id},
            headers=headers,
        )
        assert enroll_resp.status_code == 201, enroll_resp.text
        enrollment_id = enroll_resp.json()["data"]["id"]

        stu_summary = await client.get("/api/v1/students/summary", headers=headers)
        assert stu_summary.status_code == 200, stu_summary.text
        assert stu_summary.json()["data"]["enrolled_current_session"] == 1

        stu_filtered = await client.get(
            f"/api/v1/students?gender=MALE&class_group_id={cg_id}",
            headers=headers,
        )
        assert stu_filtered.status_code == 200, stu_filtered.text
        assert stu_filtered.json()["data"]["meta"]["total"] == 1
        assert stu_filtered.json()["data"]["items"][0]["current_class_name"] == "Basic 1 A"

        # Subjects summary & enriched class-subjects
        subj_summary = await client.get("/api/v1/subjects/summary", headers=headers)
        assert subj_summary.status_code == 200, subj_summary.text
        assert subj_summary.json()["data"]["class_allocations"] > 0

        cs_list = await client.get(
            f"/api/v1/class-subjects?class_group_id={cg_id}&has_teacher=false&page_size=50",
            headers=headers,
        )
        assert cs_list.status_code == 200, cs_list.text
        cs_items = cs_list.json()["data"]["items"]
        assert len(cs_items) > 0
        assert cs_items[0]["class_group_name"] == "Basic 1 A"
        cs_id = cs_items[0]["id"]

        await client.post(
            f"/api/v1/class-subjects/{cs_id}/teacher-assignments",
            json={"teacher_id": user_id},
            headers=headers,
        )

        # Mark attendance and test all 3 Attendance GET endpoints
        mark_att = await client.post(
            f"/api/v1/structure/class-groups/{cg_id}/attendance",
            json={
                "academic_term_id": term_id,
                "attendance_date": today.isoformat(),
                "records": [{"student_enrollment_id": enrollment_id, "status": "PRESENT"}],
            },
            headers=headers,
        )
        assert mark_att.status_code == 200, mark_att.text

        get_att = await client.get(
            f"/api/v1/structure/class-groups/{cg_id}/attendance?academic_term_id={term_id}",
            headers=headers,
        )
        assert get_att.status_code == 200, get_att.text
        assert get_att.json()["data"]["items"][0]["status"] == "PRESENT"

        get_att_sum = await client.get(
            f"/api/v1/structure/class-groups/{cg_id}/attendance-summary?academic_term_id={term_id}",
            headers=headers,
        )
        assert get_att_sum.status_code == 200, get_att_sum.text
        assert get_att_sum.json()["data"][0]["times_present"] == 1

        att_kpis = await client.get(
            f"/api/v1/structure/attendance/summary?academic_term_id={term_id}",
            headers=headers,
        )
        assert att_kpis.status_code == 200, att_kpis.text
        assert att_kpis.json()["data"]["classes_marked_today"] == 1

        # Assessments sheets overview
        sheets = await client.get(
            f"/api/v1/assessments/sheets?academic_term_id={term_id}&class_group_id={cg_id}",
            headers=headers,
        )
        assert sheets.status_code == 200, sheets.text
        assert sheets.json()["data"]["summary"]["total"] > 0

        # Admin dashboard, issues, teacher dashboard & audit logs
        admin_dash = await client.get("/api/v1/structure/dashboard/admin", headers=headers)
        assert admin_dash.status_code == 200, admin_dash.text
        dash_data = admin_dash.json()["data"]
        assert dash_data["students_enrolled"] == 1
        assert dash_data["active_staff"] == 1
        assert len(dash_data["term_workflow"]) == 5
        assert len(dash_data["needs_attention"]) > 0
        assert len(dash_data["recent_activities"]) > 0

        issues_resp = await client.get(
            "/api/v1/structure/dashboard/issues?issue_type=MISSING_CLASS_TEACHER",
            headers=headers,
        )
        assert issues_resp.status_code == 200, issues_resp.text
        assert len(issues_resp.json()["data"]["items"]) > 0

        teacher_dash = await client.get("/api/v1/structure/dashboard/teacher", headers=headers)
        assert teacher_dash.status_code == 200, teacher_dash.text
        t_data = teacher_dash.json()["data"]
        assert t_data["my_classes"][0]["display_name"] == "Basic 1 A"
        assert t_data["my_classes"][0]["today_attendance_marked"] is True
        assert t_data["subjects_count"] == 1

        audit_resp = await client.get("/api/v1/audit/logs", headers=headers)
        assert audit_resp.status_code == 200, audit_resp.text
        assert audit_resp.json()["data"]["meta"]["total"] > 0
        assert audit_resp.json()["data"]["items"][0]["resource_summary"]

        # Verify single-record enrichments & offerings
        cg_detail = await client.get(f"/api/v1/structure/class-groups/{cg_id}", headers=headers)
        assert cg_detail.status_code == 200, cg_detail.text
        assert cg_detail.json()["data"]["class_teacher_name"] == "TundeUpdated Balogun"
        assert cg_detail.json()["data"]["student_count"] == 1
        assert cg_detail.json()["data"]["academic_level_name"]

        stu_detail = await client.get(f"/api/v1/students/{student_id}", headers=headers)
        assert stu_detail.status_code == 200, stu_detail.text
        assert stu_detail.json()["data"]["current_class_name"] == "Basic 1 A"

        offerings_resp = await client.get("/api/v1/subjects/offerings", headers=headers)
        assert offerings_resp.status_code == 200, offerings_resp.text
        assert offerings_resp.json()["data"]["items"][0]["subject_name"]

        # Invite a TEACHER, accept invite, edit via PATCH /users/{id}, and verify Class Teacher routes
        teacher_email = f"teacher.{suffix}@olivet-test.edu.ng"
        inv_resp = await client.post(
            "/api/v1/identity/staff/invite",
            json={
                "email": teacher_email,
                "first_name": "Grace",
                "last_name": "Ojo",
                "role": "TEACHER",
                "staff_id_number": f"TCH-{suffix.upper()}",
            },
            headers=headers,
        )
        assert inv_resp.status_code == 201, inv_resp.text
        inv_token = inv_resp.json()["data"]["invitation_token"]

        acc_resp = await client.post(
            "/api/v1/identity/staff/accept-invitation",
            json={
                "invitation_token": inv_token,
                "password": "TeacherPassword123!",
                "confirm_password": "TeacherPassword123!",
            },
        )
        assert acc_resp.status_code == 200, acc_resp.text
        t_headers = {"Authorization": f"Bearer {acc_resp.json()['data']['access_token']}"}
        t_me = await client.get("/api/v1/identity/me", headers=t_headers)
        assert t_me.json()["data"]["school_type"] == "PRIMARY"
        teacher_id = t_me.json()["data"]["id"]

        # Admin edits teacher via PATCH /users/{user_id}
        edit_staff = await client.patch(
            f"/api/v1/identity/users/{teacher_id}",
            json={"specialization": "Mathematics"},
            headers=headers,
        )
        assert edit_staff.status_code == 200, edit_staff.text
        assert edit_staff.json()["data"]["specialization"] == "Mathematics"

        # Assign teacher as Class Teacher of cg_id and Subject Teacher of cs_id
        await client.patch(
            f"/api/v1/structure/class-groups/{cg_id}",
            json={"class_teacher_id": teacher_id},
            headers=headers,
        )
        await client.post(
            f"/api/v1/class-subjects/{cs_id}/teacher-assignments",
            json={"teacher_id": teacher_id},
            headers=headers,
        )

        # Teacher (no structure:manage or subjects:manage) must NOT get 403 on their routes
        assert (
            await client.get("/api/v1/structure/sessions", headers=t_headers)
        ).status_code == 200
        assert (
            await client.get(f"/api/v1/structure/sessions/{session_id}/terms", headers=t_headers)
        ).status_code == 200
        assert (
            await client.get(f"/api/v1/structure/class-groups/{cg_id}", headers=t_headers)
        ).status_code == 200
        assert (
            await client.get(f"/api/v1/structure/class-groups/{cg_id}/subjects", headers=t_headers)
        ).status_code == 200
        assert (
            await client.get(
                f"/api/v1/structure/attendance/summary?academic_term_id={term_id}",
                headers=t_headers,
            )
        ).status_code == 403
        admin_att_summary = await client.get(
            f"/api/v1/structure/attendance/summary?academic_term_id={term_id}",
            headers=headers,
        )
        assert admin_att_summary.status_code == 200, admin_att_summary.text
        assert len(admin_att_summary.json()["data"]["classes"]) >= 1
        assert any(
            c["class_group_id"] == cg_id and c["marked"] is True
            for c in admin_att_summary.json()["data"]["classes"]
        )
        t_att = await client.get(
            f"/api/v1/structure/class-groups/{cg_id}/attendance?academic_term_id={term_id}",
            headers=t_headers,
        )
        assert t_att.status_code == 200, t_att.text
        assert t_att.json()["data"]["term_status"] == "DRAFT"
        assert (
            await client.post(
                f"/api/v1/structure/class-groups/{cg_id}/attendance",
                json={
                    "academic_term_id": term_id,
                    "attendance_date": today.isoformat(),
                    "records": [{"student_enrollment_id": enrollment_id, "status": "LATE"}],
                },
                headers=t_headers,
            )
        ).status_code == 200
        assert (
            await client.get(
                f"/api/v1/assessments/definitions?academic_term_id={term_id}&class_subject_id={cs_id}",
                headers=t_headers,
            )
        ).status_code == 200
        t_sheets = await client.get(
            f"/api/v1/assessments/sheets?academic_term_id={term_id}",
            headers=t_headers,
        )
        assert t_sheets.status_code == 200, t_sheets.text
        assert t_sheets.json()["data"]["summary"]["total"] == 1

        # Report cards list (empty before compute, returns 200 with summary)
        rc_resp = await client.get(
            f"/api/v1/publication/report-cards?academic_term_id={term_id}&class_group_id={cg_id}",
            headers=headers,
        )
        assert rc_resp.status_code == 200, rc_resp.text
        assert rc_resp.json()["data"]["summary"]["students_computed"] == 0

        # 1 class teacher per class arm per session (application-level):
        # Re-saving the same class group with the same teacher succeeds
        same_cg = await client.patch(
            f"/api/v1/structure/class-groups/{cg_id}",
            json={"class_teacher_id": teacher_id},
            headers=headers,
        )
        assert same_cg.status_code == 200, same_cg.text

        # Assigning the same teacher to a different class group in the same session returns 409
        other_cgs = await client.get(
            f"/api/v1/structure/class-groups?session_id={session_id}&page_size=50",
            headers=headers,
        )
        other_cg_id = next(
            item["id"] for item in other_cgs.json()["data"]["items"] if item["id"] != cg_id
        )
        dup_cg = await client.patch(
            f"/api/v1/structure/class-groups/{other_cg_id}",
            json={"class_teacher_id": teacher_id},
            headers=headers,
        )
        assert dup_cg.status_code == 409, dup_cg.text
        assert dup_cg.json()["error"]["code"] == "CLASS_TEACHER_SESSION_CONFLICT"
