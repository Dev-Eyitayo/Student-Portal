import uuid
from datetime import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.modules.timetable.engine import (
    ClassSubjectSpec,
    RoomSpec,
    SlotSpec,
    SyncGroupSpec,
    TeacherConstraintSpec,
    intervals_overlap,
    solve_timetable,
    validate_manual_move,
)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def test_cross_category_teacher_interval_overlap():
    assert intervals_overlap(1, time(8, 0), time(8, 40), 1, time(8, 30), time(9, 5)) is True
    assert intervals_overlap(1, time(8, 0), time(8, 40), 1, time(8, 40), time(9, 20)) is False
    assert (
        intervals_overlap(1, time(8, 0), time(8, 40), 1, time(8, 40), time(9, 20), gap_minutes=5)
        is True
    )


def test_solver_handles_cross_category_teacher_breaks_double_periods_and_joint_class():
    jss_sched_id = _uuid()
    sss_sched_id = _uuid()
    shared_teacher = _uuid()
    other_teacher = _uuid()
    chem_lab_id = _uuid()

    # JSS has 35-min periods; SSS has 40-min periods with Short Break between P1 and P2
    slots = [
        SlotSpec(_uuid(), jss_sched_id, 1, 1, "P1", time(8, 0), time(8, 35), "TEACHING"),
        SlotSpec(_uuid(), jss_sched_id, 1, 2, "P2", time(8, 35), time(9, 10), "TEACHING"),
        SlotSpec(_uuid(), jss_sched_id, 1, 3, "P3", time(9, 10), time(9, 45), "TEACHING"),
        SlotSpec(_uuid(), jss_sched_id, 1, 4, "P4", time(9, 45), time(10, 20), "TEACHING"),
        SlotSpec(_uuid(), sss_sched_id, 1, 1, "P1", time(8, 0), time(8, 40), "TEACHING"),
        SlotSpec(
            _uuid(), sss_sched_id, 1, 2, "Short Break", time(8, 40), time(8, 55), "SHORT_BREAK"
        ),
        SlotSpec(_uuid(), sss_sched_id, 1, 3, "P2", time(8, 55), time(9, 35), "TEACHING"),
        SlotSpec(_uuid(), sss_sched_id, 1, 4, "P3", time(9, 35), time(10, 15), "TEACHING"),
    ]
    slots_by_id = {s.id: s for s in slots}

    jss_cg = _uuid()
    ss_cg_o = _uuid()
    ss_cg_l = _uuid()

    cs_jss_mth = ClassSubjectSpec(
        class_subject_id=_uuid(),
        class_group_id=jss_cg,
        class_group_name="JSS1 A",
        level_category="JUNIOR_SECONDARY",
        bell_schedule_id=jss_sched_id,
        subject_id=_uuid(),
        subject_code="MTH",
        subject_name="Mathematics",
        teacher_ids=(shared_teacher,),
        periods_per_week=1,
    )
    cs_ss_chm_o = ClassSubjectSpec(
        class_subject_id=_uuid(),
        class_group_id=ss_cg_o,
        class_group_name="SS2 Science O",
        level_category="SENIOR_SECONDARY",
        bell_schedule_id=sss_sched_id,
        subject_id=_uuid(),
        subject_code="CHM",
        subject_name="Chemistry",
        teacher_ids=(shared_teacher,),
        periods_per_week=2,
        double_periods_per_week=1,
        preferred_room_id=chem_lab_id,
        room_only_for_double_periods=True,
        enrollment_count=20,
    )
    cs_ss_chm_l = ClassSubjectSpec(
        class_subject_id=_uuid(),
        class_group_id=ss_cg_l,
        class_group_name="SS2 Science L",
        level_category="SENIOR_SECONDARY",
        bell_schedule_id=sss_sched_id,
        subject_id=_uuid(),
        subject_code="CHM",
        subject_name="Chemistry",
        teacher_ids=(shared_teacher,),
        periods_per_week=2,
        double_periods_per_week=1,
        preferred_room_id=chem_lab_id,
        room_only_for_double_periods=True,
        enrollment_count=20,
    )
    cs_ss_eng = ClassSubjectSpec(
        class_subject_id=_uuid(),
        class_group_id=ss_cg_o,
        class_group_name="SS2 Science O",
        level_category="SENIOR_SECONDARY",
        bell_schedule_id=sss_sched_id,
        subject_id=_uuid(),
        subject_code="ENG",
        subject_name="English",
        teacher_ids=(other_teacher,),
        periods_per_week=1,
    )

    joint = SyncGroupSpec(
        id=_uuid(),
        name="SS2 Chemistry Practical Joint",
        sync_type="JOINT_CLASS",
        class_subject_ids=(cs_ss_chm_o.class_subject_id, cs_ss_chm_l.class_subject_id),
    )

    result = solve_timetable(
        slots=slots,
        class_subjects=[cs_ss_chm_o, cs_ss_chm_l, cs_jss_mth, cs_ss_eng],
        rooms={chem_lab_id: RoomSpec(chem_lab_id, "Chemistry Lab", "CHM-LAB", capacity=45)},
        sync_groups=[joint],
        teacher_constraints={
            shared_teacher: TeacherConstraintSpec(shared_teacher, "Mr. Ade"),
            other_teacher: TeacherConstraintSpec(other_teacher, "Mrs. Bello"),
        },
    )

    assert result.success is True
    assert result.unplaced_count == 0

    chm_o_slots = [
        slots_by_id[e.timetable_slot_id]
        for e in result.entries
        if e.class_subject_id == cs_ss_chm_o.class_subject_id
    ]
    chm_l_slots = [
        slots_by_id[e.timetable_slot_id]
        for e in result.entries
        if e.class_subject_id == cs_ss_chm_l.class_subject_id
    ]
    assert len(chm_o_slots) == 2
    assert {s.id for s in chm_o_slots} == {s.id for s in chm_l_slots}
    # Double period must be P2 (08:55-09:35) and P3 (09:35-10:15), not crossing Short Break
    assert sorted(s.period_order for s in chm_o_slots) == [3, 4]

    # JSS1 Maths must NOT overlap 08:55-10:15 (so it must be placed in P1 08:00-08:35)
    jss_mth_slots = [
        slots_by_id[e.timetable_slot_id]
        for e in result.entries
        if e.class_subject_id == cs_jss_mth.class_subject_id
    ]
    assert len(jss_mth_slots) == 1
    assert jss_mth_slots[0].period_order == 1

    # Validate manual move onto Short Break fails
    break_slot = next(s for s in slots if s.slot_type == "SHORT_BREAK")
    eng_diags = validate_manual_move(
        target_slot=break_slot,
        moving_cs=cs_ss_eng,
        existing_entries=result.entries,
        slots_by_id=slots_by_id,
        cs_by_id={
            cs.class_subject_id: cs for cs in [cs_ss_chm_o, cs_ss_chm_l, cs_jss_mth, cs_ss_eng]
        },
        teacher_constraints={},
        sync_groups=[joint],
    )
    assert any(d.code == "NON_TEACHING_SLOT" for d in eng_diags)


@pytest.mark.asyncio
async def test_timetable_api_end_to_end_and_tenant_isolation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        payload = {
            "name": f"Timetable High {suffix}",
            "slug": f"timetable-high-{suffix}",
            "official_code": f"TTH-{suffix.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "12 Timetable Road, Oyo",
            "country_code": "NG",
            "admin_email": f"admin.{suffix}@timetable-test.edu.ng",
            "admin_first_name": "Seyi",
            "admin_last_name": "Adeyemi",
            "admin_password": "TimetableSecurePassword123!",
            "school_type": "SECONDARY",
        }
        reg_resp = await client.post("/api/v1/identity/schools", json=payload)
        assert reg_resp.status_code == 201, reg_resp.text

        login_resp = await client.post(
            "/api/v1/identity/login",
            json={"email": payload["admin_email"], "password": payload["admin_password"]},
        )
        assert login_resp.status_code == 200, login_resp.text
        headers = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}

        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_id": "nigeria_junior_secondary",
                "session_name": f"2025/2026 {suffix}",
                "session_start_date": "2025-09-08",
                "session_end_date": "2026-07-24",
                "include_class_groups": True,
                "arms_per_level": 1,
                "selected_subject_codes": ["ENG", "MTH", "BSC"],
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
                "start_date": "2025-09-08",
                "end_date": "2025-12-12",
                "is_current": True,
            },
            headers=headers,
        )
        assert term_resp.status_code == 201, term_resp.text
        term_id = term_resp.json()["data"]["id"]

        # Create JSS bell schedule with Short Break and Long Break across Mon-Fri
        slots_payload = []
        for day in range(1, 6):
            slots_payload.extend(
                [
                    {
                        "day_of_week": day,
                        "period_order": 1,
                        "label": "Period 1",
                        "start_time": "08:00:00",
                        "end_time": "08:40:00",
                        "slot_type": "TEACHING",
                    },
                    {
                        "day_of_week": day,
                        "period_order": 2,
                        "label": "Period 2",
                        "start_time": "08:40:00",
                        "end_time": "09:20:00",
                        "slot_type": "TEACHING",
                    },
                    {
                        "day_of_week": day,
                        "period_order": 3,
                        "label": "Short Break",
                        "start_time": "09:20:00",
                        "end_time": "09:35:00",
                        "slot_type": "SHORT_BREAK",
                    },
                    {
                        "day_of_week": day,
                        "period_order": 4,
                        "label": "Period 3",
                        "start_time": "09:35:00",
                        "end_time": "10:15:00",
                        "slot_type": "TEACHING",
                    },
                    {
                        "day_of_week": day,
                        "period_order": 5,
                        "label": "Period 4",
                        "start_time": "10:15:00",
                        "end_time": "10:55:00",
                        "slot_type": "TEACHING",
                    },
                    {
                        "day_of_week": day,
                        "period_order": 6,
                        "label": "Long Break",
                        "start_time": "10:55:00",
                        "end_time": "11:30:00",
                        "slot_type": "LONG_BREAK",
                    },
                    {
                        "day_of_week": day,
                        "period_order": 7,
                        "label": "Period 5",
                        "start_time": "11:30:00",
                        "end_time": "12:10:00",
                        "slot_type": "TEACHING",
                    },
                ]
            )
        bell_resp = await client.post(
            "/api/v1/timetable/bell-schedules",
            json={
                "academic_session_id": session_id,
                "level_category": "JUNIOR_SECONDARY",
                "name": "JSS Standard Bell",
                "slots": slots_payload,
            },
            headers=headers,
        )
        assert bell_resp.status_code == 201, bell_resp.text

        # A second bell schedule for a *different* category, deliberately using a
        # different grid shape (3 periods/day instead of 5) and clashing
        # period_order values with overlapping times. A JSS class arm's grid must
        # only ever show its own (JUNIOR_SECONDARY) schedule -- regression check
        # for a bug where the class-group grid endpoint merged every bell
        # schedule in the session instead of resolving the one that applies.
        senior_slots_payload = []
        for day in range(1, 6):
            senior_slots_payload.extend(
                [
                    {
                        "day_of_week": day,
                        "period_order": 1,
                        "label": "Period 1",
                        "start_time": "09:00:00",
                        "end_time": "09:50:00",
                        "slot_type": "TEACHING",
                    },
                    {
                        "day_of_week": day,
                        "period_order": 2,
                        "label": "Period 2",
                        "start_time": "09:50:00",
                        "end_time": "10:40:00",
                        "slot_type": "TEACHING",
                    },
                    {
                        "day_of_week": day,
                        "period_order": 3,
                        "label": "Period 3",
                        "start_time": "10:40:00",
                        "end_time": "11:30:00",
                        "slot_type": "TEACHING",
                    },
                ]
            )
        senior_bell_resp = await client.post(
            "/api/v1/timetable/bell-schedules",
            json={
                "academic_session_id": session_id,
                "level_category": "SENIOR_SECONDARY",
                "name": "SS Standard Bell",
                "slots": senior_slots_payload,
            },
            headers=headers,
        )
        assert senior_bell_resp.status_code == 201, senior_bell_resp.text

        # Create opt-in shared room
        room_resp = await client.post(
            "/api/v1/timetable/rooms",
            json={
                "name": "Basic Science Lab",
                "code": "BSC-LAB",
                "room_type": "SCIENCE_LAB",
                "capacity": 50,
            },
            headers=headers,
        )
        assert room_resp.status_code == 201, room_resp.text

        # Create timetable draft & generate (CELERY_TASK_ALWAYS_EAGER=true runs synchronously in test env)
        sched_resp = await client.post(
            "/api/v1/timetable/schedules",
            json={"academic_term_id": term_id, "name": "First Term Timetable"},
            headers=headers,
        )
        assert sched_resp.status_code == 201, sched_resp.text
        tt_id = sched_resp.json()["data"]["id"]

        gen_resp = await client.post(
            f"/api/v1/timetable/schedules/{tt_id}/generate", headers=headers
        )
        assert gen_resp.status_code == 202, gen_resp.text

        master_resp = await client.get(
            f"/api/v1/timetable/schedules/{tt_id}/master", headers=headers
        )
        assert master_resp.status_code == 200, master_resp.text
        entries = master_resp.json()["data"]["entries"]
        assert len(entries) > 0

        # Regression check: a single class arm's grid must only show its own
        # bell schedule's slots, even though a second, unrelated-category bell
        # schedule also exists in this session.
        jss_arm = next(
            g
            for g in master_resp.json()["data"]["class_groups"]
            if g["level_category"] == "JUNIOR_SECONDARY"
        )
        grid_resp = await client.get(
            f"/api/v1/timetable/schedules/{tt_id}/class-groups/{jss_arm['id']}",
            headers=headers,
        )
        assert grid_resp.status_code == 200, grid_resp.text
        grid_slots = grid_resp.json()["data"]["slots"]
        assert grid_slots, "expected the JSS arm's own bell schedule slots"
        assert {s["bell_schedule_id"] for s in grid_slots} == {bell_resp.json()["data"]["id"]}
        day_period_pairs = [(s["day_of_week"], s["period_order"]) for s in grid_slots]
        assert len(day_period_pairs) == len(set(day_period_pairs)), (
            "duplicate (day, period_order) slots -- two bell schedules got merged"
        )

        # NL assistant parse
        nl_resp = await client.post(
            "/api/v1/timetable/assistant/parse",
            json={"academic_session_id": session_id, "prompt": "Seyi shouldn't teach on Friday"},
            headers=headers,
        )
        assert nl_resp.status_code == 200, nl_resp.text
        assert nl_resp.json()["data"]["action_type"] == "CREATE_TEACHER_AVAILABILITY"

        # Cross-tenant isolation check
        other_suffix = uuid.uuid4().hex[:6]
        other_payload = {
            **payload,
            "name": f"Other School {other_suffix}",
            "slug": f"other-tt-{other_suffix}",
            "official_code": f"OTT-{other_suffix.upper()}",
            "admin_email": f"other.{other_suffix}@timetable-test.edu.ng",
        }
        assert (
            await client.post("/api/v1/identity/schools", json=other_payload)
        ).status_code == 201
        other_login = await client.post(
            "/api/v1/identity/login",
            json={
                "email": other_payload["admin_email"],
                "password": other_payload["admin_password"],
            },
        )
        other_headers = {"Authorization": f"Bearer {other_login.json()['data']['access_token']}"}
        iso_resp = await client.get(
            f"/api/v1/timetable/schedules/{tt_id}/master", headers=other_headers
        )
        assert iso_resp.status_code == 404
