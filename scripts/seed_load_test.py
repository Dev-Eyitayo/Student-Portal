"""Multi-tenant staging seeder for k6 load testing.

Creates N isolated tenant schools (official_code: LOAD-01 .. LOAD-NN) with:
- SchoolConfiguration, Admin + Teacher staff accounts
- Multi-term history: First Term (PUBLISHED report cards, SubjectResults, ResultAccessTokens, QR payloads)
  and Second Term (active term with DRAFT/APPROVED score sheets, AssessmentScores, Attendance)
- BellSchedule, TimetableSlot, and a DRAFT Timetable for timetable generation & view tests
- Exports a JSON manifest to load_tests/k6/fixtures/seed_manifest.json for k6 SharedArray.

Never run against production. Intended for local/staging benchmarking only.

Usage:
    .venv/bin/python -m scripts.seed_load_test --schools 5 --students-per-arm 25 --reset
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import secrets
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401 - register all SQLAlchemy mappers
from app.core.database import _tenant_school_id, async_session_factory
from app.core.security import hash_password
from app.modules.academic_structure.models import (
    AcademicLevel,
    AcademicSession,
    AcademicTerm,
    ClassArm,
    ClassGroup,
    Department,
)
from app.modules.assessments.models import (
    AssessmentDefinition,
    AssessmentScore,
    ScoreSheetStatus,
)
from app.modules.audit.models import AuditLog
from app.modules.computation.models import SubjectResult, TermReportCard
from app.modules.distribution.models import ResultAccessToken
from app.modules.distribution.services import _hash_token, _sign_qr_payload
from app.modules.identity.models import (
    School,
    SchoolConfiguration,
    SchoolRolePermission,
    StaffProfile,
    User,
    UserPermissionGrant,
)
from app.modules.identity.schemas import SchoolType
from app.modules.identity.seed import seed_system_permissions
from app.modules.identity.services import SCHOOL_DEFAULTS
from app.modules.students.models import (
    AttendanceRecord,
    AttendanceSummary,
    Student,
    StudentEnrollment,
)
from app.modules.subjects.models import (
    ClassSubject,
    StudentSubjectRegistration,
    Subject,
    SubjectOffering,
    TeacherAssignment,
)
from app.modules.timetable.models import (
    BellSchedule,
    Timetable,
    TimetableEntry,
    TimetableEntryTeacher,
    TimetableSlot,
)

LOAD_TEST_PASSWORD = "LoadTest123!"
DEFAULT_MANIFEST_PATH = Path("load_tests/k6/fixtures/seed_manifest.json")

FIRST_NAMES = [
    "Chinedu",
    "Amina",
    "Tunde",
    "Favour",
    "David",
    "Zainab",
    "Ibrahim",
    "Chisom",
    "Olumide",
    "Halima",
    "Kelechi",
    "Blessing",
    "Samuel",
    "Mariam",
    "Damilola",
    "Ngozi",
]

LAST_NAMES = [
    "Okafor",
    "Yusuf",
    "Balogun",
    "Adeyemi",
    "Nwosu",
    "Adebayo",
    "Musa",
    "Eze",
    "Ojo",
    "Lawal",
    "Okonkwo",
    "Suleiman",
]

ASSESSMENT_SPECS = [
    ("First C.A. Test", "TEST1", "CONTINUOUS_ASSESSMENT", Decimal("10.00"), Decimal("10.00"), 1),
    ("Second C.A. Test", "TEST2", "CONTINUOUS_ASSESSMENT", Decimal("10.00"), Decimal("10.00"), 2),
    (
        "Assignments & Notes",
        "ASSIGN",
        "CONTINUOUS_ASSESSMENT",
        Decimal("10.00"),
        Decimal("10.00"),
        3,
    ),
    ("Terminal Examination", "EXAM", "EXAMINATION", Decimal("70.00"), Decimal("70.00"), 4),
]


async def _set_tenant_context(db: AsyncSession, school_id: UUID | None) -> None:
    _tenant_school_id.set(str(school_id) if school_id else "")
    await db.execute(
        text("SELECT set_config('app.current_school_id', :sid, true)"),
        {"sid": str(school_id) if school_id else ""},
    )


async def _reset_load_test_schools(db: AsyncSession) -> None:
    await db.execute(text("SELECT set_config('app.is_super_admin', 'true', true)"))
    school_ids = (
        (await db.execute(select(School.id).where(School.official_code.like("LOAD-%"))))
        .scalars()
        .all()
    )
    if not school_ids:
        return

    tenant_models = [
        ResultAccessToken,
        TermReportCard,
        SubjectResult,
        AssessmentScore,
        ScoreSheetStatus,
        AssessmentDefinition,
        AttendanceSummary,
        AttendanceRecord,
        StudentSubjectRegistration,
        StudentEnrollment,
        Student,
        TimetableEntryTeacher,
        TimetableEntry,
        Timetable,
        TimetableSlot,
        BellSchedule,
        TeacherAssignment,
        ClassSubject,
        SubjectOffering,
        Subject,
        ClassGroup,
        ClassArm,
        Department,
        AcademicTerm,
        AcademicSession,
        AcademicLevel,
        UserPermissionGrant,
        SchoolRolePermission,
        AuditLog,
        StaffProfile,
        User,
        SchoolConfiguration,
    ]
    for model in tenant_models:
        await db.execute(delete(model).where(model.school_id.in_(school_ids)))
    await db.execute(delete(School).where(School.id.in_(school_ids)))
    await db.commit()
    print(f"Reset {len(school_ids)} existing LOAD-* schools.")


def _grade_for_score(score: Decimal) -> tuple[str, str]:
    val = float(score)
    if val >= 75:
        return "A1", "Excellent"
    if val >= 70:
        return "B2", "Very Good"
    if val >= 65:
        return "B3", "Good"
    if val >= 60:
        return "C4", "Credit"
    if val >= 55:
        return "C5", "Credit"
    if val >= 50:
        return "C6", "Credit"
    if val >= 45:
        return "D7", "Pass"
    if val >= 40:
        return "E8", "Pass"
    return "F9", "Fail"


async def _seed_single_school(
    db: AsyncSession,
    school_idx: int,
    students_per_arm: int,
    shared_password_hash: str,
    rng: random.Random,
) -> dict[str, Any]:
    code = f"LOAD-{school_idx:02d}"
    slug = f"load-test-school-{school_idx:02d}"
    now_utc = datetime.now(UTC)

    existing = (
        await db.execute(select(School).where(School.official_code == code))
    ).scalar_one_or_none()
    if existing is not None:
        raise RuntimeError(
            f"School {code} already exists. Re-run with --reset to recreate load-test schools."
        )

    school = School(
        slug=slug,
        name=f"Olivet Load Test School {school_idx:02d}",
        official_code=code,
        country_code="NG",
        state="Oyo",
        city="Oyo",
        address=f"{school_idx} Load Test Avenue, Oyo",
        school_type="SECONDARY",
        is_active=True,
    )
    db.add(school)
    await db.flush()
    await _set_tenant_context(db, school.id)

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
            show_department_position_on_report_card=True,
            show_level_position_on_report_card=True,
            show_subject_position_on_report_card=True,
            setup_wizard_completed=True,
            setup_wizard_dismissed=False,
        )
    )

    # Admin + 4 Teachers
    admin_email = f"admin@loadtest-{school_idx:02d}.olivet.edu.ng"
    admin = User(
        school_id=school.id,
        email=admin_email,
        password_hash=shared_password_hash,
        role="SCHOOL_ADMIN",
        first_name="Admin",
        last_name=f"School{school_idx:02d}",
        phone_number=f"+23480100{school_idx:05d}",
        is_active=True,
    )
    db.add(admin)
    await db.flush()
    db.add(
        StaffProfile(
            school_id=school.id,
            user_id=admin.id,
            staff_id_number=f"{code}/STF/001",
            employment_status="ACTIVE",
            specialization="School Administration",
        )
    )

    teachers: list[User] = []
    for t_idx in range(1, 5):
        teacher = User(
            school_id=school.id,
            email=f"teacher-{t_idx}@loadtest-{school_idx:02d}.olivet.edu.ng",
            password_hash=shared_password_hash,
            role="TEACHER",
            first_name=f"Teacher{t_idx}",
            last_name=f"School{school_idx:02d}",
            phone_number=f"+234802{school_idx:02d}{t_idx:05d}",
            is_active=True,
        )
        db.add(teacher)
        teachers.append(teacher)
    await db.flush()

    for t_idx, teacher in enumerate(teachers, start=1):
        db.add(
            StaffProfile(
                school_id=school.id,
                user_id=teacher.id,
                staff_id_number=f"{code}/STF/{t_idx + 1:03d}",
                employment_status="ACTIVE",
                specialization="Core & Science Subjects",
            )
        )

    # Session & 2 Terms (First Term = PUBLISHED history; Second Term = ACTIVE)
    session = AcademicSession(
        school_id=school.id,
        name="2026/2027",
        start_date=date(2026, 5, 1),
        end_date=date(2027, 4, 30),
        is_current=True,
    )
    db.add(session)
    await db.flush()

    term1 = AcademicTerm(
        school_id=school.id,
        academic_session_id=session.id,
        name="First Term",
        term_order=1,
        start_date=date(2026, 5, 15),
        end_date=date(2026, 8, 15),
        is_current=False,
        is_locked_for_scoring=True,
    )
    term2 = AcademicTerm(
        school_id=school.id,
        academic_session_id=session.id,
        name="Second Term",
        term_order=2,
        start_date=date(2026, 9, 14),
        end_date=date(2026, 12, 18),
        is_current=True,
        is_locked_for_scoring=False,
    )
    db.add_all([term1, term2])
    await db.flush()

    # Levels, Departments, Arms, ClassGroups
    jss1 = AcademicLevel(
        school_id=school.id,
        name="JSS 1",
        code="JSS1",
        level_order=1,
        category="JUNIOR_SECONDARY",
    )
    ss1 = AcademicLevel(
        school_id=school.id,
        name="SS 1",
        code="SS1",
        level_order=4,
        category="SENIOR_SECONDARY",
    )
    db.add_all([jss1, ss1])
    await db.flush()

    dept_sci = Department(school_id=school.id, name="Science", code="SCI")
    dept_art = Department(school_id=school.id, name="Arts", code="ART")
    db.add_all([dept_sci, dept_art])
    await db.flush()

    arm_o = ClassArm(school_id=school.id, name="O")
    arm_l = ClassArm(school_id=school.id, name="L")
    db.add_all([arm_o, arm_l])
    await db.flush()

    cg_specs = [
        (jss1.id, None, arm_o.id, "JSS 1 O", teachers[0].id),
        (jss1.id, None, arm_l.id, "JSS 1 L", teachers[1].id),
        (ss1.id, dept_sci.id, arm_o.id, "SS 1 Science O", teachers[2].id),
        (ss1.id, dept_art.id, arm_l.id, "SS 1 Arts L", teachers[3].id),
    ]
    class_groups: list[ClassGroup] = []
    for level_id, dept_id, arm_id, disp_name, ct_id in cg_specs:
        cg = ClassGroup(
            school_id=school.id,
            academic_session_id=session.id,
            academic_level_id=level_id,
            department_id=dept_id,
            class_arm_id=arm_id,
            display_name=disp_name,
            class_teacher_id=ct_id,
        )
        db.add(cg)
        class_groups.append(cg)
    await db.flush()

    # Subjects, Offerings, ClassSubjects, TeacherAssignments
    subject_defs = [
        ("ENG", "English Language", False),
        ("MTH", "Mathematics", False),
        ("CIV", "Civic Education", False),
        ("PHY", "Physics", False),
        ("CHM", "Chemistry", False),
        ("LIT", "Literature in English", True),
    ]
    subjects_by_code: dict[str, Subject] = {}
    for scode, sname, is_elec in subject_defs:
        subj = Subject(
            school_id=school.id,
            code=scode,
            name=sname,
            is_elective=is_elec,
            is_trade_subject=False,
        )
        db.add(subj)
        subjects_by_code[scode] = subj
    await db.flush()

    level_subject_codes: dict[tuple[UUID, UUID | None], list[str]] = {
        (jss1.id, None): ["ENG", "MTH", "CIV"],
        (ss1.id, dept_sci.id): ["ENG", "MTH", "CIV", "PHY", "CHM"],
        (ss1.id, dept_art.id): ["ENG", "MTH", "CIV", "LIT"],
    }
    offerings_map: dict[tuple[UUID, UUID | None, str], SubjectOffering] = {}
    for (lvl_id, dept_id), codes in level_subject_codes.items():
        for scode in codes:
            off = SubjectOffering(
                school_id=school.id,
                subject_id=subjects_by_code[scode].id,
                academic_session_id=session.id,
                academic_level_id=lvl_id,
                department_id=dept_id,
            )
            db.add(off)
            offerings_map[(lvl_id, dept_id, scode)] = off
    await db.flush()

    class_subjects_by_cg: dict[UUID, list[ClassSubject]] = {cg.id: [] for cg in class_groups}
    teacher_cg_map: dict[UUID, set[UUID]] = {t.id: set() for t in teachers}
    teacher_cs_map: dict[UUID, list[UUID]] = {t.id: [] for t in teachers}
    all_class_subjects: list[ClassSubject] = []

    cs_counter = 0
    for cg in class_groups:
        codes = level_subject_codes[(cg.academic_level_id, cg.department_id)]
        for scode in codes:
            off = offerings_map[(cg.academic_level_id, cg.department_id, scode)]
            cs = ClassSubject(
                school_id=school.id,
                class_group_id=cg.id,
                subject_offering_id=off.id,
            )
            db.add(cs)
            class_subjects_by_cg[cg.id].append(cs)
            all_class_subjects.append(cs)
            cs_counter += 1
    await db.flush()

    for idx, cs in enumerate(all_class_subjects):
        assigned_teacher = teachers[idx % len(teachers)]
        db.add(
            TeacherAssignment(
                school_id=school.id,
                class_subject_id=cs.id,
                teacher_id=assigned_teacher.id,
                assigned_by_id=admin.id,
                is_active=True,
            )
        )
        teacher_cg_map[assigned_teacher.id].add(cs.class_group_id)
        teacher_cs_map[assigned_teacher.id].append(cs.id)

    # Students, Enrollments, SubjectRegistrations
    students: list[Student] = []
    enrollments: list[StudentEnrollment] = []
    enrollments_by_cg: dict[UUID, list[StudentEnrollment]] = {cg.id: [] for cg in class_groups}

    student_seq = 0
    for cg in class_groups:
        for _ in range(students_per_arm):
            student_seq += 1
            gender = "M" if student_seq % 2 == 1 else "F"
            dob = date(
                2011 if cg.academic_level_id == jss1.id else 2009,
                (student_seq % 12) + 1,
                (student_seq % 27) + 1,
            )
            adm_no = f"{code}/2026/{student_seq:04d}"
            student = Student(
                school_id=school.id,
                admission_number=adm_no,
                first_name=rng.choice(FIRST_NAMES),
                last_name=rng.choice(LAST_NAMES),
                date_of_birth=dob,
                gender=gender,
                guardian_name=f"Guardian {student_seq:04d}",
                guardian_email=f"guardian{student_seq}@loadtest-{school_idx:02d}.olivet.edu.ng",
                guardian_phone=f"+234809{school_idx:02d}{student_seq:05d}",
                enrollment_date=date(2026, 9, 14),
                status="ACTIVE",
            )
            db.add(student)
            students.append(student)
    await db.flush()

    idx = 0
    for cg in class_groups:
        for _ in range(students_per_arm):
            student = students[idx]
            idx += 1
            enr = StudentEnrollment(
                school_id=school.id,
                student_id=student.id,
                academic_session_id=session.id,
                class_group_id=cg.id,
                enrollment_status="ENROLLED",
            )
            db.add(enr)
            enrollments.append(enr)
            enrollments_by_cg[cg.id].append(enr)
    await db.flush()

    student_by_id = {s.id: s for s in students}
    for cg in class_groups:
        for enr in enrollments_by_cg[cg.id]:
            for cs in class_subjects_by_cg[cg.id]:
                db.add(
                    StudentSubjectRegistration(
                        school_id=school.id,
                        student_enrollment_id=enr.id,
                        class_subject_id=cs.id,
                    )
                )

    # Assessment Definitions & Scores for Term 1 (PUBLISHED) and Term 2 (ACTIVE)
    defns_by_term_and_cs: dict[tuple[UUID, UUID], list[AssessmentDefinition]] = {}
    for term in (term1, term2):
        for cs in all_class_subjects:
            defns: list[AssessmentDefinition] = []
            for aname, acode, atype, max_score, weight_pct, order in ASSESSMENT_SPECS:
                defn = AssessmentDefinition(
                    school_id=school.id,
                    academic_term_id=term.id,
                    class_subject_id=cs.id,
                    name=aname,
                    code=acode,
                    assessment_type=atype,
                    max_score=max_score,
                    weight_percentage=weight_pct,
                    sequence_order=order,
                )
                db.add(defn)
                defns.append(defn)
            defns_by_term_and_cs[(term.id, cs.id)] = defns
    await db.flush()

    # Term 1 Scores, SubjectResults, TermReportCards, ResultAccessTokens
    frozen_snapshot = {
        "grading_scale": defaults["grading_scale_schema"],
        "assessment_weights": defaults["assessment_weight_schema"],
    }
    term1_scores: list[AssessmentScore] = []
    term1_subject_results: list[SubjectResult] = []
    term1_totals_by_enr: dict[UUID, Decimal] = {e.id: Decimal("0.00") for e in enrollments}
    term1_subj_count_by_enr: dict[UUID, int] = {e.id: 0 for e in enrollments}

    for cs in all_class_subjects:
        cg_enrs = enrollments_by_cg[cs.class_group_id]
        defns = defns_by_term_and_cs[(term1.id, cs.id)]
        scores_for_cs: list[tuple[StudentEnrollment, Decimal]] = []

        for enr in cg_enrs:
            ca1 = Decimal(str(rng.randint(5, 10)))
            ca2 = Decimal(str(rng.randint(5, 10)))
            ass = Decimal(str(rng.randint(6, 10)))
            exam = Decimal(str(rng.randint(32, 68)))
            raw_vals = [ca1, ca2, ass, exam]
            total = sum(raw_vals, Decimal("0.00"))
            scores_for_cs.append((enr, total))
            term1_totals_by_enr[enr.id] += total
            term1_subj_count_by_enr[enr.id] += 1

            for defn, raw_val in zip(defns, raw_vals, strict=True):
                term1_scores.append(
                    AssessmentScore(
                        school_id=school.id,
                        assessment_definition_id=defn.id,
                        student_enrollment_id=enr.id,
                        raw_score=raw_val,
                        score_status="SCORED",
                        recorded_by_id=admin.id,
                        last_modified_by_id=admin.id,
                    )
                )

        totals_only = [t for _, t in scores_for_cs]
        highest = max(totals_only)
        lowest = min(totals_only)
        avg = (sum(totals_only, Decimal("0.00")) / Decimal(len(totals_only))).quantize(
            Decimal("0.01")
        )
        sorted_totals = sorted(totals_only, reverse=True)

        for enr, total in scores_for_cs:
            grade, remark = _grade_for_score(total)
            pos = sorted_totals.index(total) + 1
            term1_subject_results.append(
                SubjectResult(
                    school_id=school.id,
                    academic_term_id=term1.id,
                    class_subject_id=cs.id,
                    student_enrollment_id=enr.id,
                    total_score=total,
                    grade=grade,
                    remark=remark,
                    subject_position=pos,
                    class_highest_score=highest,
                    class_lowest_score=lowest,
                    class_average_score=avg,
                    status="PUBLISHED",
                    approved_by_id=admin.id,
                    approved_at=now_utc - timedelta(days=31),
                    frozen_config_snapshot=frozen_snapshot,
                )
            )

        db.add(
            ScoreSheetStatus(
                school_id=school.id,
                academic_term_id=term1.id,
                class_subject_id=cs.id,
                status="APPROVED",
                submitted_by_id=admin.id,
                submitted_at=now_utc - timedelta(days=32),
                approved_by_id=admin.id,
                approved_at=now_utc - timedelta(days=31),
            )
        )

    db.add_all(term1_scores)
    db.add_all(term1_subject_results)

    term1_cards: list[TermReportCard] = []
    for cg in class_groups:
        cg_enrs = enrollments_by_cg[cg.id]
        pct_list = sorted(
            [
                (
                    term1_totals_by_enr[e.id] / Decimal(max(1, term1_subj_count_by_enr[e.id]))
                ).quantize(Decimal("0.01"))
                for e in cg_enrs
            ],
            reverse=True,
        )
        for enr in cg_enrs:
            obtained = term1_totals_by_enr[enr.id]
            obtainable = Decimal(term1_subj_count_by_enr[enr.id] * 100)
            pct = (obtained / obtainable * Decimal("100.00")).quantize(Decimal("0.01"))
            pos = pct_list.index(pct) + 1
            term1_cards.append(
                TermReportCard(
                    school_id=school.id,
                    academic_term_id=term1.id,
                    student_enrollment_id=enr.id,
                    total_obtainable_marks=obtainable,
                    total_marks_obtained=obtained,
                    overall_percentage=pct,
                    class_position=pos,
                    total_students_in_arm=len(cg_enrs),
                    level_position=pos,
                    total_students_in_level=len(cg_enrs),
                    times_school_opened=60,
                    times_present=57,
                    times_absent=3,
                    teacher_comment="Good performance.",
                    principal_comment="Keep it up.",
                    publication_status="PUBLISHED",
                    published_at=now_utc - timedelta(days=30),
                    frozen_config_snapshot=frozen_snapshot,
                )
            )
    db.add_all(term1_cards)
    await db.flush()

    # ResultAccessTokens with high max_access_limit for k6 burst verification
    result_tokens_manifest: list[dict[str, str]] = []
    enr_by_id = {e.id: e for e in enrollments}
    for card in term1_cards:
        raw_token = f"lt_{school_idx:02d}_{secrets.token_urlsafe(16)}"
        db.add(
            ResultAccessToken(
                school_id=school.id,
                term_report_card_id=card.id,
                token_hash=_hash_token(raw_token),
                expires_at=now_utc + timedelta(days=365),
                access_count=0,
                max_access_limit=100000,
                is_revoked=False,
            )
        )
        stu = student_by_id[enr_by_id[card.student_enrollment_id].student_id]
        qr_payload = _sign_qr_payload(
            stu.admission_number,
            "T1",
            card.total_marks_obtained,
        )
        result_tokens_manifest.append(
            {
                "token": raw_token,
                "dob": stu.date_of_birth.isoformat(),
                "admission_number": stu.admission_number,
                "qr_payload": qr_payload,
            }
        )

    # Term 2 Scores (cg[0..1] 100% APPROVED for calculate-term/broadsheet; cg[2..3] DRAFT for bulk-entry)
    approved_cg_ids = {class_groups[0].id, class_groups[1].id}
    term2_scores: list[AssessmentScore] = []
    for cs in all_class_subjects:
        cg_enrs = enrollments_by_cg[cs.class_group_id]
        defns = defns_by_term_and_cs[(term2.id, cs.id)]
        for enr in cg_enrs:
            raw_vals = [
                Decimal(str(rng.randint(5, 10))),
                Decimal(str(rng.randint(5, 10))),
                Decimal(str(rng.randint(6, 10))),
                Decimal(str(rng.randint(30, 65))),
            ]
            for defn, raw_val in zip(defns, raw_vals, strict=True):
                term2_scores.append(
                    AssessmentScore(
                        school_id=school.id,
                        assessment_definition_id=defn.id,
                        student_enrollment_id=enr.id,
                        raw_score=raw_val,
                        score_status="SCORED",
                        recorded_by_id=admin.id,
                        last_modified_by_id=admin.id,
                    )
                )
        sheet_status = "APPROVED" if cs.class_group_id in approved_cg_ids else "DRAFT"
        db.add(
            ScoreSheetStatus(
                school_id=school.id,
                academic_term_id=term2.id,
                class_subject_id=cs.id,
                status=sheet_status,
                submitted_by_id=admin.id if sheet_status == "APPROVED" else None,
                submitted_at=now_utc - timedelta(days=1) if sheet_status == "APPROVED" else None,
                approved_by_id=admin.id if sheet_status == "APPROVED" else None,
                approved_at=now_utc if sheet_status == "APPROVED" else None,
            )
        )
    db.add_all(term2_scores)

    # Term 2 Attendance Records (5 recent school days) + AttendanceSummary
    att_dates = [date(2026, 11, 18) + timedelta(days=d) for d in range(5)]
    for enr in enrollments:
        present_count = 0
        absent_count = 0
        for adate in att_dates:
            status_val = "PRESENT" if rng.random() < 0.92 else "ABSENT"
            if status_val == "PRESENT":
                present_count += 1
            else:
                absent_count += 1
            db.add(
                AttendanceRecord(
                    school_id=school.id,
                    academic_term_id=term2.id,
                    student_enrollment_id=enr.id,
                    attendance_date=adate,
                    status=status_val,
                    recorded_by_id=admin.id,
                )
            )
        db.add(
            AttendanceSummary(
                school_id=school.id,
                academic_term_id=term2.id,
                student_enrollment_id=enr.id,
                times_school_opened=len(att_dates),
                times_present=present_count,
                times_absent=absent_count,
                source="DAILY",
                updated_by_id=admin.id,
            )
        )

    # Timetable BellSchedule + Slots + DRAFT Timetable
    bell = BellSchedule(
        school_id=school.id,
        academic_session_id=session.id,
        level_category="SENIOR_SECONDARY",
        name="Standard Secondary Schedule",
        is_active=True,
    )
    db.add(bell)
    await db.flush()

    for day in range(1, 6):
        for period in range(1, 7):
            start_hr = 7 + period
            db.add(
                TimetableSlot(
                    school_id=school.id,
                    bell_schedule_id=bell.id,
                    day_of_week=day,
                    period_order=period,
                    label=f"Period {period}",
                    start_time=time(start_hr, 0),
                    end_time=time(start_hr, 40),
                    slot_type="TEACHING",
                )
            )

    timetable = Timetable(
        school_id=school.id,
        academic_term_id=term2.id,
        version=1,
        name="Second Term Draft Timetable",
        status="DRAFT",
        generation_status="IDLE",
    )
    db.add(timetable)
    await db.flush()

    term2_defn_ids = [
        str(d.id) for cs in all_class_subjects for d in defns_by_term_and_cs[(term2.id, cs.id)]
    ]

    return {
        "school_id": str(school.id),
        "official_code": code,
        "admission_prefix": f"{code}/2026/",
        "admin": {"email": admin_email, "password": LOAD_TEST_PASSWORD},
        "teachers": [
            {
                "email": t.email,
                "password": LOAD_TEST_PASSWORD,
                "form_class_group_id": str(class_groups[t_idx].id),
                "class_group_ids": [str(cg_id) for cg_id in sorted(teacher_cg_map[t.id], key=str)],
                "class_subject_ids": [str(cs_id) for cs_id in teacher_cs_map[t.id]],
            }
            for t_idx, t in enumerate(teachers)
        ],
        "active_term_id": str(term2.id),
        "published_term_id": str(term1.id),
        "timetable_id": str(timetable.id),
        "approved_class_group_ids": [str(cg_id) for cg_id in approved_cg_ids],
        "draft_class_subject_ids": [
            str(cs.id) for cs in all_class_subjects if cs.class_group_id not in approved_cg_ids
        ],
        "class_groups": [
            {
                "id": str(cg.id),
                "name": cg.display_name,
                "enrollment_ids": [str(e.id) for e in enrollments_by_cg[cg.id]],
                "class_subject_ids": [str(cs.id) for cs in class_subjects_by_cg[cg.id]],
            }
            for cg in class_groups
        ],
        "assessment_definitions": {
            str(cs.id): [str(d.id) for d in defns_by_term_and_cs[(term2.id, cs.id)]]
            for cs in all_class_subjects
        },
        "assessment_definition_ids": term2_defn_ids,
        "student_ids": [str(s.id) for s in students],
        "result_tokens": result_tokens_manifest,
    }


async def seed_load_test(
    *,
    schools: int,
    students_per_arm: int,
    reset: bool,
    output_path: Path,
) -> None:
    rng = random.Random(20261125)
    shared_password_hash = hash_password(LOAD_TEST_PASSWORD)

    if reset:
        async with async_session_factory() as db:
            await _reset_load_test_schools(db)

    async with async_session_factory() as db:
        await seed_system_permissions(db)
        await db.commit()

    school_manifests: list[dict[str, Any]] = []
    for i in range(1, schools + 1):
        async with async_session_factory() as db:
            await db.execute(text("SELECT set_config('app.is_super_admin', 'true', true)"))
            manifest_entry = await _seed_single_school(
                db, i, students_per_arm, shared_password_hash, rng
            )
            await db.commit()
            school_manifests.append(manifest_entry)
            print(
                f"Seeded {manifest_entry['official_code']} "
                f"({len(manifest_entry['student_ids'])} students, "
                f"{len(manifest_entry['result_tokens'])} result tokens)."
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "schools_count": len(school_manifests),
        "students_per_arm": students_per_arm,
        "schools": school_manifests,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote k6 seed manifest to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed multi-tenant data for k6 load testing.")
    parser.add_argument("--schools", type=int, default=5, help="Number of LOAD-* schools to seed.")
    parser.add_argument(
        "--students-per-arm",
        type=int,
        default=25,
        help="Students per class arm (4 arms per school).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe existing LOAD-* schools before seeding.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Output path for the k6 JSON manifest.",
    )
    args = parser.parse_args()
    asyncio.run(
        seed_load_test(
            schools=args.schools,
            students_per_arm=args.students_per_arm,
            reset=args.reset,
            output_path=args.output,
        )
    )


if __name__ == "__main__":
    main()
