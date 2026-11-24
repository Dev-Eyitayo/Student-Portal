"""Idempotent timetable demo seed script for Olivet Baptist High School.

Seeds realistic Nigerian Senior Secondary timetable constraints so you can
immediately click "Generate Timetable" in the UI:
  - Senior Secondary bell schedule (50-min periods, 2x 5-min short breaks,
    1x 30-min long break, closing at 1:40 PM)
  - Shared bottleneck labs (Chemistry Lab, Physics Lab, Biology Lab, ICT Room)
  - Subject weekly frequencies: 3 periods/week for core subjects (ENG, MTH, CIV)
    and 2 periods/week for all other subjects
  - Sample Combined Class (joint SS 2 Chemistry) & Simultaneous Electives (SS 1 Trade)
  - Sample Teacher Availability & Daily Load Cap rules

Usage:
    .venv/bin/python -m scripts.seed_timetable_demo
"""

import asyncio
import logging
from datetime import time
from uuid import UUID

from sqlalchemy import select, text

import app.models  # noqa: F401 - register all SQLAlchemy mappers
from app.core.database import async_session_factory
from app.core.security import hash_password
from app.modules.academic_structure.models import (
    AcademicLevel,
    AcademicSession,
    ClassGroup,
)
from app.modules.identity.models import School, User
from app.modules.subjects.models import ClassSubject, Subject, SubjectOffering, TeacherAssignment
from app.modules.timetable.models import (
    BellSchedule,
    Room,
    SubjectSchedulingRule,
    TeacherAvailability,
    TimetableSlot,
    TimetableSyncGroup,
    TimetableSyncGroupItem,
)

CORE_SUBJECT_CODES = {"ENG", "MTH", "CIV"}
LAB_SUBJECT_MAP = {
    "CHM": ("Chemistry Lab", "CHM-LAB", "SCIENCE_LAB", 60),
    "PHY": ("Physics Lab", "PHY-LAB", "SCIENCE_LAB", 60),
    "BIO": ("Biology Lab", "BIO-LAB", "SCIENCE_LAB", 60),
}

SENIOR_SLOTS = [
    ("Morning Assembly", time(7, 45), time(8, 0), "ASSEMBLY"),
    ("Period 1", time(8, 0), time(8, 40), "TEACHING"),
    ("Period 2", time(8, 40), time(9, 20), "TEACHING"),
    ("Short Break 1", time(9, 20), time(9, 25), "SHORT_BREAK"),
    ("Period 3", time(9, 25), time(10, 5), "TEACHING"),
    ("Period 4", time(10, 5), time(10, 45), "TEACHING"),
    ("Long Break", time(10, 45), time(11, 15), "LONG_BREAK"),
    ("Period 5", time(11, 15), time(11, 55), "TEACHING"),
    ("Short Break 2", time(11, 55), time(12, 0), "SHORT_BREAK"),
    ("Period 6", time(12, 0), time(12, 40), "TEACHING"),
    ("Period 7", time(12, 40), time(13, 20), "TEACHING"),
    ("Period 8", time(13, 20), time(14, 0), "TEACHING"),
]


THURSDAY_SLOTS = [
    ("Morning Assembly", time(7, 45), time(8, 0), "ASSEMBLY"),
    ("Environmental Sanitation", time(8, 0), time(8, 40), "CLUBS_SPORTS"),
    ("Environmental Sanitation", time(8, 40), time(9, 20), "CLUBS_SPORTS"),
    ("Period 1", time(9, 20), time(10, 0), "TEACHING"),
    ("Short Break 1", time(10, 0), time(10, 5), "SHORT_BREAK"),
    ("Period 2", time(10, 5), time(10, 45), "TEACHING"),
    ("Long Break", time(10, 45), time(11, 15), "LONG_BREAK"),
    ("Period 3", time(11, 15), time(11, 55), "TEACHING"),
    ("Short Break 2", time(11, 55), time(12, 0), "SHORT_BREAK"),
    ("Period 4", time(12, 0), time(12, 40), "TEACHING"),
    ("Period 5", time(12, 40), time(13, 20), "TEACHING"),
    ("Period 6", time(13, 20), time(14, 0), "TEACHING"),
]


def _build_olivet_week_slots(school_id: UUID, bell_id: UUID) -> list[TimetableSlot]:
    slots: list[TimetableSlot] = []
    for day in range(1, 6):
        if day == 4:
            for order, (label, start_t, end_t, slot_type) in enumerate(THURSDAY_SLOTS, start=1):
                slots.append(
                    TimetableSlot(
                        school_id=school_id,
                        bell_schedule_id=bell_id,
                        day_of_week=day,
                        period_order=order,
                        label=label,
                        start_time=start_t,
                        end_time=end_t,
                        slot_type=slot_type,
                    )
                )
            continue

        for order, (label, start_t, end_t, slot_type) in enumerate(SENIOR_SLOTS, start=1):
            # Friday closes by 1:00 PM (Period 6 ends at 12:40 PM)
            if day == 5 and end_t > time(13, 0):
                continue
            row_label = label
            row_type = slot_type
            # Tuesday last 2 periods (afternoon): Literary & Debate
            if day == 2 and label in ("Period 7", "Period 8"):
                row_label = "Literary & Debate"
                row_type = "CLUBS_SPORTS"
            slots.append(
                TimetableSlot(
                    school_id=school_id,
                    bell_schedule_id=bell_id,
                    day_of_week=day,
                    period_order=order,
                    label=row_label,
                    start_time=start_t,
                    end_time=end_t,
                    slot_type=row_type,
                )
            )
    return slots


async def seed_olivet_timetable() -> None:
    logging.disable(logging.INFO)
    async with async_session_factory() as db:
        await db.execute(text("SELECT set_config('app.is_super_admin', 'true', true)"))

        school = (
            await db.execute(
                select(School).where(
                    School.slug == "olivet-baptist-high-school",
                    School.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if school is None:
            raise RuntimeError("School 'olivet-baptist-high-school' not found.")

        await db.execute(
            text("SELECT set_config('app.current_school_id', :sid, true)"),
            {"sid": str(school.id)},
        )

        session = (
            await db.execute(
                select(AcademicSession)
                .where(
                    AcademicSession.school_id == school.id,
                    AcademicSession.deleted_at.is_(None),
                )
                .order_by(AcademicSession.is_current.desc(), AcademicSession.start_date.desc())
            )
        ).scalar_one_or_none()
        if session is None:
            raise RuntimeError("No academic session found for Olivet Baptist High School.")

        # 1. Bell Schedule for SENIOR_SECONDARY
        bell = (
            await db.execute(
                select(BellSchedule).where(
                    BellSchedule.school_id == school.id,
                    BellSchedule.academic_session_id == session.id,
                    BellSchedule.level_category == "SENIOR_SECONDARY",
                    BellSchedule.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if bell is None:
            bell = BellSchedule(
                school_id=school.id,
                academic_session_id=session.id,
                level_category="SENIOR_SECONDARY",
                name="Senior Secondary Standard Bell (40m)",
                is_active=True,
            )
            db.add(bell)
            await db.flush()
            for slot in _build_olivet_week_slots(school.id, bell.id):
                db.add(slot)
            print(f"Created Bell Schedule: {bell.name} (34 teaching slots/wk, Tue L&D, Thu Env, Fri 12:40 PM)")
        else:
            existing_slots = (
                (
                    await db.execute(
                        select(TimetableSlot).where(
                            TimetableSlot.bell_schedule_id == bell.id,
                            TimetableSlot.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            desired_slots = _build_olivet_week_slots(school.id, bell.id)
            bell.name = "Senior Secondary Standard Bell (40m)"
            for sl in existing_slots:
                await db.delete(sl)
            await db.flush()
            for slot in desired_slots:
                db.add(slot)
            print(
                f"Updated Bell Schedule: {bell.name} ({len(desired_slots)} slots: 34 teaching, Tue PM L&D, Thu AM Env, Fri 12:40 PM)"
            )

        # 2. Shared Labs / Rooms
        rooms_by_code: dict[str, Room] = {}
        existing_rooms = (
            (
                await db.execute(
                    select(Room).where(Room.school_id == school.id, Room.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        for r in existing_rooms:
            rooms_by_code[r.code] = r

        for _subj_code, (r_name, r_code, r_type, cap) in LAB_SUBJECT_MAP.items():
            if r_code not in rooms_by_code:
                room = Room(
                    school_id=school.id,
                    name=r_name,
                    code=r_code,
                    room_type=r_type,
                    capacity=cap,
                )
                db.add(room)
                await db.flush()
                rooms_by_code[r_code] = room
                print(f"Created Shared Room: {r_name} ({r_code})")

        # 3. Subject Scheduling Rules (3/wk for core, 2/wk for other subjects)
        subjects = (
            (
                await db.execute(
                    select(Subject).where(
                        Subject.school_id == school.id, Subject.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_rules = {
            r.subject_id: r
            for r in (
                await db.execute(
                    select(SubjectSchedulingRule).where(
                        SubjectSchedulingRule.school_id == school.id,
                        SubjectSchedulingRule.academic_session_id == session.id,
                        SubjectSchedulingRule.academic_level_id.is_(None),
                        SubjectSchedulingRule.department_id.is_(None),
                        SubjectSchedulingRule.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        }

        created_rules = 0
        updated_rules = 0
        for subj in subjects:
            code = subj.code.upper()
            is_core = code in CORE_SUBJECT_CODES
            lab_info = LAB_SUBJECT_MAP.get(code)
            room_id: UUID | None = rooms_by_code[lab_info[1]].id if lab_info else None
            has_double = lab_info is not None

            rule = existing_rules.get(subj.id)
            if rule is None:
                db.add(
                    SubjectSchedulingRule(
                        school_id=school.id,
                        academic_session_id=session.id,
                        subject_id=subj.id,
                        periods_per_week=3 if is_core else 2,
                        double_periods_per_week=1 if has_double else 0,
                        allow_double_period_split=False,
                        max_periods_per_day=2,
                        prefer_morning=is_core,
                        preferred_room_id=room_id,
                        room_only_for_double_periods=True,
                    )
                )
                created_rules += 1
            else:
                rule.periods_per_week = 3 if is_core else 2
                rule.double_periods_per_week = 1 if has_double else 0
                rule.prefer_morning = is_core
                rule.preferred_room_id = room_id
                updated_rules += 1
        print(f"Synced subject rules: {created_rules} created, {updated_rules} updated (3/wk core, 2/wk others).")

        # 4. Combined Class (SS 2 Science O + L Chemistry) & Simultaneous Electives (SS 1 Trade)
        existing_syncs = {
            sg.name
            for sg in (
                await db.execute(
                    select(TimetableSyncGroup).where(
                        TimetableSyncGroup.school_id == school.id,
                        TimetableSyncGroup.academic_session_id == session.id,
                        TimetableSyncGroup.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        }

        cs_rows = (
            await db.execute(
                select(ClassSubject, ClassGroup, Subject, AcademicLevel)
                .join(ClassGroup, ClassSubject.class_group_id == ClassGroup.id)
                .join(SubjectOffering, ClassSubject.subject_offering_id == SubjectOffering.id)
                .join(Subject, SubjectOffering.subject_id == Subject.id)
                .join(AcademicLevel, ClassGroup.academic_level_id == AcademicLevel.id)
                .where(
                    ClassSubject.school_id == school.id,
                    ClassGroup.academic_session_id == session.id,
                    ClassSubject.deleted_at.is_(None),
                )
            )
        ).all()

        if "SS 2 Chemistry Joint Lab (O + L)" not in existing_syncs:
            ss2_chem = [
                cs.id
                for cs, cg, subj, lvl in cs_rows
                if lvl.name == "SS 2"
                and subj.code == "CHM"
                and cg.display_name in ("SS 2 Science O", "SS 2 Science L")
            ]
            if len(ss2_chem) >= 2:
                sg = TimetableSyncGroup(
                    school_id=school.id,
                    academic_session_id=session.id,
                    name="SS 2 Chemistry Joint Lab (O + L)",
                    sync_type="JOINT_CLASS",
                )
                db.add(sg)
                await db.flush()
                for csid in ss2_chem:
                    db.add(
                        TimetableSyncGroupItem(
                            school_id=school.id,
                            sync_group_id=sg.id,
                            class_subject_id=csid,
                        )
                    )
                print("Created Joint Class: SS 2 Chemistry Joint Lab (O + L)")

        # Group CRS + IRS and Trade electives per arm so arms stay <= 30 periods/week
        by_arm: dict[str, dict[str, UUID]] = {}
        for cs, cg, subj, _lvl in cs_rows:
            by_arm.setdefault(cg.display_name, {})[subj.code] = cs.id

        for arm_name, subj_map in sorted(by_arm.items()):
            rel_ids = [subj_map[c] for c in ("CRS", "IRS") if c in subj_map]
            rel_group_name = f"{arm_name} Religion Electives (CRS/IRS)"
            if len(rel_ids) >= 2 and rel_group_name not in existing_syncs:
                sg = TimetableSyncGroup(
                    school_id=school.id,
                    academic_session_id=session.id,
                    name=rel_group_name,
                    sync_type="PARALLEL_SPLIT",
                )
                db.add(sg)
                await db.flush()
                for csid in rel_ids:
                    db.add(
                        TimetableSyncGroupItem(
                            school_id=school.id,
                            sync_group_id=sg.id,
                            class_subject_id=csid,
                        )
                    )
                existing_syncs.add(rel_group_name)

            trade_ids = [
                subj_map[c] for c in ("CAT", "DPR", "GAR", "FIS", "MKT") if c in subj_map
            ]
            trade_group_name = f"{arm_name} Trade Electives"
            if (
                len(trade_ids) >= 2
                and trade_group_name not in existing_syncs
                and not (arm_name == "SS 1 Arts B" and "SS 1 Arts B Trade Electives Split" in existing_syncs)
            ):
                sg = TimetableSyncGroup(
                    school_id=school.id,
                    academic_session_id=session.id,
                    name=trade_group_name,
                    sync_type="PARALLEL_SPLIT",
                )
                db.add(sg)
                await db.flush()
                for csid in trade_ids:
                    db.add(
                        TimetableSyncGroupItem(
                            school_id=school.id,
                            sync_group_id=sg.id,
                            class_subject_id=csid,
                        )
                    )
                existing_syncs.add(trade_group_name)
        print("Ensured Simultaneous Electives (CRS/IRS & Trade subjects) across all arms.")

        # 5. Ensure enough teachers (~28) & rebalance assignments so no teacher > 25 periods/week
        extra_teacher_names = [
            ("Adebisi", "Ogunleye"),
            ("Chinedu", "Okafor"),
            ("Kemi", "Adedoyin"),
            ("Yusuf", "Abdullahi"),
            ("Nneka", "Uche"),
            ("Babatunde", "Fashola"),
            ("Zainab", "Suleiman"),
            ("Oluwaseun", "Akintola"),
            ("Ifeoma", "Nwachukwu"),
            ("Damilola", "Oladipo"),
            ("Hauwa", "Danladi"),
            ("Rotimi", "Williams"),
            ("Chisom", "Okeke"),
            ("Sola", "Ogundipe"),
            ("Aminu", "Bello"),
            ("Titilayo", "Aina"),
            ("Femi", "Osho"),
            ("Blessing", "Etim"),
            ("Taiwo", "Olawale"),
            ("Nadia", "Mohammed"),
            ("Gideon", "Adebisi"),
            ("Rukayat", "Lawal"),
        ]
        teachers = (
            (
                await db.execute(
                    select(User).where(
                        User.school_id == school.id,
                        User.role == "TEACHER",
                        User.is_active.is_(True),
                        User.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_emails = {t.email for t in teachers}
        if len(teachers) < 32:
            pw_hash = hash_password("OlivetTeacher123!")
            for fn, ln in extra_teacher_names:
                if len(teachers) >= 32:
                    break
                email = f"{fn.lower()}.{ln.lower()}@obhs-oyo.edu.ng"
                if email in existing_emails:
                    continue
                u = User(
                    school_id=school.id,
                    email=email,
                    password_hash=pw_hash,
                    first_name=fn,
                    last_name=ln,
                    role="TEACHER",
                    is_active=True,
                )
                db.add(u)
                await db.flush()
                teachers.append(u)
                existing_emails.add(email)
            print(f"Ensured {len(teachers)} active teachers for realistic load distribution.")

        # Rebalance TeacherAssignments across teachers so each teacher <= 24 periods/week
        tas = (
            (
                await db.execute(
                    select(TeacherAssignment).where(
                        TeacherAssignment.school_id == school.id,
                        TeacherAssignment.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        cs_periods = {
            cs.id: (3 if subj.code.upper() in CORE_SUBJECT_CODES else 2)
            for cs, _cg, subj, _lvl in cs_rows
        }
        teacher_loads: dict[UUID, int] = {t.id: 0 for t in teachers}
        friday_off_tid = teachers[0].id if teachers else None
        load_cap_tid = teachers[1].id if len(teachers) > 1 else None
        rebalanced = 0
        for ta in tas:
            p = cs_periods.get(ta.class_subject_id, 2)
            cap = 16 if ta.teacher_id in (friday_off_tid, load_cap_tid) else 22
            cur_load = teacher_loads.get(ta.teacher_id, 0)
            if ta.teacher_id in teacher_loads and cur_load + p <= cap:
                teacher_loads[ta.teacher_id] = cur_load + p
            else:
                least_tid = min(
                    teacher_loads,
                    key=lambda tid: (
                        teacher_loads[tid]
                        + (8 if tid in (friday_off_tid, load_cap_tid) else 0)
                    ),
                )
                ta.teacher_id = least_tid
                teacher_loads[least_tid] += p
                rebalanced += 1
        if rebalanced:
            print(f"Rebalanced {rebalanced} teacher assignments (max load now {max(teacher_loads.values())} periods/wk).")

        existing_avails = {
            av.teacher_id
            for av in (
                await db.execute(
                    select(TeacherAvailability).where(
                        TeacherAvailability.school_id == school.id,
                        TeacherAvailability.academic_session_id == session.id,
                        TeacherAvailability.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        }
        if len(teachers) >= 1 and teachers[0].id not in existing_avails:
            db.add(
                TeacherAvailability(
                    school_id=school.id,
                    academic_session_id=session.id,
                    teacher_id=teachers[0].id,
                    day_of_week=5,
                    availability_type="UNAVAILABLE",
                    min_gap_minutes_between_categories=0,
                )
            )
            print(f"Marked {teachers[0].first_name} {teachers[0].last_name} unavailable on Fridays.")
        if len(teachers) >= 2 and teachers[1].id not in existing_avails:
            db.add(
                TeacherAvailability(
                    school_id=school.id,
                    academic_session_id=session.id,
                    teacher_id=teachers[1].id,
                    day_of_week=None,
                    availability_type="LOAD_CAP",
                    max_periods_per_day=4,
                    min_gap_minutes_between_categories=0,
                )
            )
            print(f"Capped {teachers[1].first_name} {teachers[1].last_name} to max 4 classes/day.")

        await db.commit()
        print("Timetable demo seed complete!")


if __name__ == "__main__":
    asyncio.run(seed_olivet_timetable())
