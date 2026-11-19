import uuid
from datetime import date, timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.config import Settings
from app.core.database import async_session_factory
from app.core.mail import MailClient
from app.main import app
from app.modules.computation.models import TermReportCard
from app.modules.identity.seed import seed_system_permissions


@pytest.fixture
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


async def _register_and_login(
    client: AsyncClient, suffix: str, *, school_type: str = "PRIMARY"
) -> tuple[str, dict[str, str]]:
    email = f"m5a.admin.{suffix}@olivet-test.edu.ng"
    password = "StrongAdminPassword123!"
    resp = await client.post(
        "/api/v1/identity/schools",
        json={
            "name": f"Olivet M5A School {suffix}",
            "slug": f"olivet-m5a-{suffix}",
            "official_code": f"M5A-{suffix.upper()}",
            "state": "Oyo",
            "city": "Oyo",
            "address": "1 School Lane",
            "country_code": "NG",
            "admin_email": email,
            "admin_first_name": "Bisi",
            "admin_last_name": "Ojo",
            "admin_password": password,
            "school_type": school_type,
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
    return school_id, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_senviok_mail_client_payload_and_headers(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = '{"id": "msg_123"}'

        def json(self) -> dict[str, str]:
            return {"id": "msg_123"}

    class _FakeAsyncClient:
        def __init__(self, *, base_url: str, timeout: int):
            captured["base_url"] = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, path: str, *, json: dict, headers: dict):
            captured["path"] = path
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    mail_settings = Settings(
        MAIL_PROVIDER="senviok",
        MAIL_BASE_URL="https://api.senviok.live/",
        MAIL_API_KEY="svk_test_key",
        MAIL_FROM_NAME="Olivet Records",
        DEFAULT_FROM_EMAIL="records@olivet.edu.ng",
    )
    client = MailClient(mail_settings)
    msg_id = await client.send(
        to_email="parent@example.com",
        to_name="Ngozi Eze",
        subject="First Term Report Ready",
        html="<p>View report</p>",
    )

    assert msg_id == "msg_123"
    assert captured["base_url"] == "https://api.senviok.live"
    assert captured["path"] == "/v1/emails"
    assert captured["headers"] == {
        "Authorization": "Bearer svk_test_key",
        "Content-Type": "application/json",
    }
    assert captured["json"] == {
        "from": "Olivet Records <records@olivet.edu.ng>",
        "to": "Ngozi Eze <parent@example.com>",
        "subject": "First Term Report Ready",
        "html": "<p>View report</p>",
    }


@pytest.mark.asyncio
async def test_attendance_marking_summary_and_report_card_verification_lifecycle(
    seed_permissions_fixture,
):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_id, headers = await _register_and_login(client, suffix)

        today = date.today()
        session_start = today - timedelta(days=30)
        session_end = today + timedelta(days=60)

        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_type": "PRIMARY",
                "session_name": f"2026/2027 {suffix}",
                "session_start_date": session_start.isoformat(),
                "session_end_date": session_end.isoformat(),
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
                "start_date": session_start.isoformat(),
                "end_date": (today + timedelta(days=30)).isoformat(),
                "is_current": True,
            },
            headers=headers,
        )
        assert term_resp.status_code == 201, term_resp.text
        term_id = term_resp.json()["data"]["id"]

        cg_resp = await client.get(
            f"/api/v1/structure/class-groups?session_id={session_id}&page_size=50",
            headers=headers,
        )
        assert cg_resp.status_code == 200, cg_resp.text
        basic1_a = next(
            g for g in cg_resp.json()["data"]["items"] if g["display_name"] == "Basic 1 A"
        )
        cg_id = basic1_a["id"]

        dob_str = "2018-04-12"
        adm_no = f"PRM-{suffix}-001"
        student_resp = await client.post(
            "/api/v1/students",
            json={
                "admission_number": adm_no,
                "first_name": "Amaka",
                "last_name": "Nwosu",
                "date_of_birth": dob_str,
                "gender": "FEMALE",
                "guardian_name": "Chidi Nwosu",
                "guardian_email": f"chidi.{suffix}@example.com",
                "guardian_phone": "+2348011112222",
                "enrollment_date": session_start.isoformat(),
            },
            headers=headers,
        )
        assert student_resp.status_code == 201, student_resp.text
        student_id = student_resp.json()["data"]["id"]

        enroll_resp = await client.post(
            f"/api/v1/students/{student_id}/enrollments",
            json={"class_group_id": cg_id},
            headers=headers,
        )
        assert enroll_resp.status_code == 201, enroll_resp.text
        enrollment_id = enroll_resp.json()["data"]["id"]

        # Future date must be rejected
        future_resp = await client.post(
            f"/api/v1/structure/class-groups/{cg_id}/attendance",
            json={
                "academic_term_id": term_id,
                "attendance_date": (today + timedelta(days=1)).isoformat(),
                "records": [{"student_enrollment_id": enrollment_id, "status": "PRESENT"}],
            },
            headers=headers,
        )
        assert future_resp.status_code == 422, future_resp.text

        # Backdated entry without reason must be rejected
        yesterday = today - timedelta(days=1)
        backdate_no_reason = await client.post(
            f"/api/v1/structure/class-groups/{cg_id}/attendance",
            json={
                "academic_term_id": term_id,
                "attendance_date": yesterday.isoformat(),
                "records": [{"student_enrollment_id": enrollment_id, "status": "ABSENT"}],
            },
            headers=headers,
        )
        assert backdate_no_reason.status_code == 422, backdate_no_reason.text

        # Backdated entry with reason succeeds, and today's entry succeeds
        r1 = await client.post(
            f"/api/v1/structure/class-groups/{cg_id}/attendance",
            json={
                "academic_term_id": term_id,
                "attendance_date": yesterday.isoformat(),
                "reason": "Register synced late",
                "records": [{"student_enrollment_id": enrollment_id, "status": "ABSENT"}],
            },
            headers=headers,
        )
        assert r1.status_code == 200, r1.text

        r2 = await client.post(
            f"/api/v1/enrollments/{enrollment_id}/attendance",
            json={
                "academic_term_id": term_id,
                "attendance_date": today.isoformat(),
                "status": "PRESENT",
            },
            headers=headers,
        )
        assert r2.status_code == 200, r2.text

        summary_resp = await client.get(
            f"/api/v1/enrollments/{enrollment_id}/attendance-summary?academic_term_id={term_id}",
            headers=headers,
        )
        assert summary_resp.status_code == 200, summary_resp.text
        summary = summary_resp.json()["data"]
        assert summary["times_school_opened"] == 2
        assert summary["times_present"] == 1
        assert summary["times_absent"] == 1
        assert summary["source"] == "DAILY"

        # Cross-tenant isolation check
        other_suffix = uuid.uuid4().hex[:6]
        _, other_headers = await _register_and_login(client, other_suffix)
        iso_resp = await client.get(
            f"/api/v1/enrollments/{enrollment_id}/attendance-summary?academic_term_id={term_id}",
            headers=other_headers,
        )
        assert iso_resp.status_code == 404, iso_resp.text

        # Enter, submit, approve scores for all 10 core subjects, calculate & publish
        regs_resp = await client.get(
            f"/api/v1/enrollments/{enrollment_id}/subject-registrations?page_size=50",
            headers=headers,
        )
        assert regs_resp.status_code == 200, regs_resp.text
        cs_ids = [item["class_subject_id"] for item in regs_resp.json()["data"]["items"]]
        assert len(cs_ids) == 10

        for cs_id in cs_ids:
            defs_resp = await client.get(
                f"/api/v1/assessments/definitions?academic_term_id={term_id}&class_subject_id={cs_id}",
                headers=headers,
            )
            assert defs_resp.status_code == 200, defs_resp.text
            for d in defs_resp.json()["data"]["items"]:
                score_val = "60" if d["code"] == "EXAM" else "8"
                bulk_resp = await client.post(
                    "/api/v1/assessments/scores/bulk-entry",
                    json={
                        "class_subject_id": cs_id,
                        "assessment_definition_id": d["id"],
                        "scores": [
                            {
                                "student_enrollment_id": enrollment_id,
                                "score_status": "SCORED",
                                "raw_score": score_val,
                            }
                        ],
                    },
                    headers=headers,
                )
                assert bulk_resp.status_code == 200, bulk_resp.text

            sub_resp = await client.post(
                "/api/v1/assessments/submit",
                json={"academic_term_id": term_id, "class_subject_id": cs_id},
                headers=headers,
            )
            assert sub_resp.status_code == 200, sub_resp.text
            app_resp = await client.post(
                "/api/v1/assessments/approve",
                json={"academic_term_id": term_id, "class_subject_id": cs_id},
                headers=headers,
            )
            assert app_resp.status_code == 200, app_resp.text

        calc_resp = await client.post(
            "/api/v1/computation/calculate-term",
            json={"academic_term_id": term_id, "class_group_id": cg_id},
            headers=headers,
        )
        assert calc_resp.status_code == 200, calc_resp.text

        # Set remarks on report card before publication
        async with async_session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.current_school_id', :sid, true)"),
                {"sid": school_id},
            )
            card_id = str(
                (
                    await session.execute(
                        select(TermReportCard.id).where(
                            TermReportCard.student_enrollment_id == uuid.UUID(enrollment_id),
                            TermReportCard.academic_term_id == uuid.UUID(term_id),
                        )
                    )
                ).scalar_one()
            )

        remarks_resp = await client.patch(
            f"/api/v1/publication/report-cards/{card_id}/remarks",
            json={
                "teacher_comment": "Diligent pupil.",
                "principal_comment": "Keep it up.",
            },
            headers=headers,
        )
        assert remarks_resp.status_code == 200, remarks_resp.text

        pub_resp = await client.post(
            "/api/v1/publication/publish-term",
            json={"academic_term_id": term_id},
            headers=headers,
        )
        assert pub_resp.status_code == 202, pub_resp.text
        links = pub_resp.json()["data"]["result_links"]
        assert len(links) == 1
        result_token = links[0]["token"]

        # Remarks cannot be edited after publication
        locked_remarks = await client.patch(
            f"/api/v1/publication/report-cards/{card_id}/remarks",
            json={"teacher_comment": "Tamper"},
            headers=headers,
        )
        assert locked_remarks.status_code == 409, locked_remarks.text

        # Wrong DOB rejected
        bad_dob = await client.get(
            f"/api/v1/results/verify/{result_token}?dob=2015-01-01",
        )
        assert bad_dob.status_code == 403, bad_dob.text

        # Valid DOB returns complete frozen report payload
        verify_resp = await client.get(
            f"/api/v1/results/verify/{result_token}?dob={dob_str}",
        )
        assert verify_resp.status_code == 200, verify_resp.text
        report = verify_resp.json()["data"]
        assert report["student"]["department"] is None
        assert report["student"]["class_group"] == "Basic 1 A"
        assert report["attendance"] == {
            "times_school_opened": 2,
            "times_present": 1,
            "times_absent": 1,
        }
        assert report["summary"]["class_teacher_comment"] == "Diligent pupil."
        assert report["summary"]["principal_comment"] == "Keep it up."
        assert len(report["subject_breakdown"]) == 10
        assert report["subject_breakdown"][0]["grade"] == "A"

        # QR verification
        qr_payload = report["verification"]["qr_payload"]
        qr_ok = await client.get(f"/api/v1/results/verify-qr?payload={qr_payload}")
        assert qr_ok.status_code == 200, qr_ok.text
        assert qr_ok.json()["data"]["is_authentic"] is True

        qr_tampered = await client.get(f"/api/v1/results/verify-qr?payload={qr_payload[:-4]}0000")
        assert qr_tampered.status_code == 422, qr_tampered.text

        # Queue Senviok/console notifications
        notify_resp = await client.post(
            "/api/v1/publication/notify-term",
            json={"academic_term_id": term_id, "result_links": links},
            headers=headers,
        )
        assert notify_resp.status_code == 202, notify_resp.text
        assert notify_resp.json()["data"]["notifications_queued"] == 1


@pytest.mark.asyncio
async def test_teacher_and_class_discovery_endpoints(seed_permissions_fixture):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        _, headers = await _register_and_login(client, suffix)

        me_before = await client.get("/api/v1/identity/me", headers=headers)
        assert me_before.status_code == 200, me_before.text
        user_id = me_before.json()["data"]["id"]
        assert me_before.json()["data"]["assigned_class_groups"] == []

        today = date.today()
        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_type": "PRIMARY",
                "session_name": f"2026/2027 Disc {suffix}",
                "session_start_date": (today - timedelta(days=30)).isoformat(),
                "session_end_date": (today + timedelta(days=60)).isoformat(),
                "include_class_groups": True,
            },
            headers=headers,
        )
        assert apply_resp.status_code == 201, apply_resp.text
        session_id = apply_resp.json()["data"]["academic_session_id"]

        cg_resp = await client.get(
            f"/api/v1/structure/class-groups?session_id={session_id}&page_size=50",
            headers=headers,
        )
        assert cg_resp.status_code == 200, cg_resp.text
        basic1_a = next(
            g for g in cg_resp.json()["data"]["items"] if g["display_name"] == "Basic 1 A"
        )
        cg_id = basic1_a["id"]

        assign_ct = await client.patch(
            f"/api/v1/structure/class-groups/{cg_id}",
            json={"class_teacher_id": user_id},
            headers=headers,
        )
        assert assign_ct.status_code == 200, assign_ct.text

        cg_subjects_before = await client.get(
            f"/api/v1/structure/class-groups/{cg_id}/subjects",
            headers=headers,
        )
        assert cg_subjects_before.status_code == 200, cg_subjects_before.text
        subject_items = cg_subjects_before.json()["data"]
        assert len(subject_items) > 0
        cs_id = subject_items[0]["class_subject_id"]

        assign_subj = await client.post(
            f"/api/v1/class-subjects/{cs_id}/teacher-assignments",
            json={"teacher_id": user_id},
            headers=headers,
        )
        assert assign_subj.status_code == 201, assign_subj.text

        me_after = await client.get("/api/v1/identity/me", headers=headers)
        assert me_after.status_code == 200, me_after.text
        assigned_cgs = me_after.json()["data"]["assigned_class_groups"]
        assert len(assigned_cgs) == 1
        assert assigned_cgs[0]["id"] == cg_id
        assert assigned_cgs[0]["display_name"] == "Basic 1 A"

        my_assignments = await client.get(
            "/api/v1/class-subjects/my-assignments",
            headers=headers,
        )
        assert my_assignments.status_code == 200, my_assignments.text
        assignment_items = my_assignments.json()["data"]["items"]
        assert any(item["class_subject_id"] == cs_id for item in assignment_items)

        cg_subjects_after = await client.get(
            f"/api/v1/structure/class-groups/{cg_id}/subjects",
            headers=headers,
        )
        assert cg_subjects_after.status_code == 200, cg_subjects_after.text
        target_subj = next(
            s for s in cg_subjects_after.json()["data"] if s["class_subject_id"] == cs_id
        )
        assert any(t["teacher_id"] == user_id for t in target_subj["teachers"])
