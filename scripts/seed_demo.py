"""Idempotent realistic demo seed script for Olivet Baptist High School, Oyo.

Usage:
    .venv/bin/python -m scripts.seed_demo
"""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.security import hash_password
from app.modules.academic_structure.models import AcademicSession, AcademicTerm, ClassGroup
from app.modules.academic_structure.schemas import StructureTemplateApplyRequest, TermCreateRequest
from app.modules.academic_structure.services import apply_structure_template, create_term
from app.modules.assessments.models import AssessmentDefinition, AssessmentScore
from app.modules.audit.services import AuditContext
from app.modules.identity.models import School, SchoolConfiguration, StaffProfile, User
from app.modules.identity.schemas import SchoolType
from app.modules.identity.seed import seed_system_permissions
from app.modules.identity.services import SCHOOL_DEFAULTS
from app.modules.students.models import (
    AttendanceRecord,
    AttendanceSummary,
    Student,
    StudentEnrollment,
)
from app.modules.subjects.models import ClassSubject, Subject, SubjectOffering, TeacherAssignment

DEMO_PASSWORD = "OlivetDemo123!"


async def _set_tenant_context(db: AsyncSession, school_id: UUID) -> None:
    await db.execute(
        text("SELECT set_config('app.current_school_id', :sid, false)"),
        {"sid": str(school_id)},
    )


async def _get_or_create_school(db: AsyncSession) -> School:
    school = (
        await db.execute(select(School).where(School.official_code == "OBHS-OYO"))
    ).scalar_one_or_none()
    if school is not None:
        return school

    school = School(
        slug="olivet-baptist-high-school",
        name="Olivet Baptist High School",
        official_code="OBHS-OYO",
        country_code="NG",
        state="Oyo",
        city="Oyo",
        address="Olivet Heights, Oyo, Oyo State",
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
    await db.flush()
    return school


async def _get_or_create_staff(
    db: AsyncSession,
    school_id: UUID,
    *,
    email: str,
    first_name: str,
    last_name: str,
    phone: str,
    role: str,
    staff_id_number: str,
    specialization: str,
    employment_status: str = "ACTIVE",
) -> User:
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        return existing

    user = User(
        school_id=school_id,
        email=email,
        password_hash=hash_password(DEMO_PASSWORD),
        first_name=first_name,
        last_name=last_name,
        phone_number=phone,
        role=role,
        is_active=(employment_status == "ACTIVE"),
    )
    db.add(user)
    await db.flush()

    db.add(
        StaffProfile(
            school_id=school_id,
            user_id=user.id,
            staff_id_number=staff_id_number,
            employment_status=employment_status,
            specialization=specialization,
        )
    )
    await db.flush()
    return user


async def seed_demo() -> None:
    async with async_session_factory() as db:
        await seed_system_permissions(db)
        school = await _get_or_create_school(db)
        await _set_tenant_context(db, school.id)

        admin = await _get_or_create_staff(
            db,
            school.id,
            email="folake.adebayo@olivet.edu.ng",
            first_name="Folake",
            last_name="Adebayo",
            phone="+2348034451092",
            role="SCHOOL_ADMIN",
            staff_id_number="OBHS/STF/001",
            specialization="School Administration",
        )
        officer = await _get_or_create_staff(
            db,
            school.id,
            email="kunle.adeyemi@olivet.edu.ng",
            first_name="Kunle",
            last_name="Adeyemi",
            phone="+2348031122334",
            role="ACADEMIC_OFFICER",
            staff_id_number="OBHS/STF/002",
            specialization="Academic Records",
        )
        tunde = await _get_or_create_staff(
            db,
            school.id,
            email="tunde.balogun@olivet.edu.ng",
            first_name="Tunde",
            last_name="Balogun",
            phone="+2348023344556",
            role="TEACHER",
            staff_id_number="OBHS/STF/003",
            specialization="Mathematics",
        )
        grace = await _get_or_create_staff(
            db,
            school.id,
            email="grace.ojo@olivet.edu.ng",
            first_name="Grace",
            last_name="Ojo",
            phone="+2348056677889",
            role="TEACHER",
            staff_id_number="OBHS/STF/004",
            specialization="Chemistry",
        )
        ibrahim = await _get_or_create_staff(
            db,
            school.id,
            email="ibrahim.musa@olivet.edu.ng",
            first_name="Ibrahim",
            last_name="Musa",
            phone="+2348067788990",
            role="TEACHER",
            staff_id_number="OBHS/STF/005",
            specialization="Basic Science",
        )
        chidinma = await _get_or_create_staff(
            db,
            school.id,
            email="chidinma.nwosu@olivet.edu.ng",
            first_name="Chidinma",
            last_name="Nwosu",
            phone="+2348078899001",
            role="TEACHER",
            staff_id_number="OBHS/STF/006",
            specialization="English Language",
        )
        await _get_or_create_staff(
            db,
            school.id,
            email="aisha.adamu@olivet.edu.ng",
            first_name="Aisha",
            last_name="Adamu",
            phone="+2348091122334",
            role="TEACHER",
            staff_id_number="OBHS/STF/007",
            specialization="Biology",
            employment_status="INVITED",
        )

        audit = AuditContext(actor_id=admin.id, school_id=school.id, actor_ip="127.0.0.1")

        today = date.today()
        session = (
            await db.execute(
                select(AcademicSession).where(
                    AcademicSession.school_id == school.id,
                    AcademicSession.name == "2026/2027",
                )
            )
        ).scalar_one_or_none()
        if session is None:
            applied = await apply_structure_template(
                db,
                school.id,
                StructureTemplateApplyRequest(
                    template_type="SECONDARY",
                    session_name="2026/2027",
                    session_start_date=today - timedelta(days=45),
                    session_end_date=today + timedelta(days=270),
                    include_class_groups=True,
                ),
                audit,
            )
            session = (
                await db.execute(
                    select(AcademicSession).where(AcademicSession.id == applied.academic_session_id)
                )
            ).scalar_one()

        term = (
            await db.execute(
                select(AcademicTerm).where(
                    AcademicTerm.school_id == school.id,
                    AcademicTerm.academic_session_id == session.id,
                    AcademicTerm.term_order == 1,
                )
            )
        ).scalar_one_or_none()
        if term is None:
            await _set_tenant_context(db, school.id)
            term = await create_term(
                db,
                school.id,
                session.id,
                TermCreateRequest(
                    name="First Term",
                    term_order=1,
                    start_date=today - timedelta(days=45),
                    end_date=today + timedelta(days=45),
                    is_current=True,
                ),
                audit,
            )

        await _set_tenant_context(db, school.id)

        class_groups = list(
            (
                await db.execute(
                    select(ClassGroup).where(
                        ClassGroup.school_id == school.id,
                        ClassGroup.academic_session_id == session.id,
                        ClassGroup.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        cg_by_name = {cg.display_name: cg for cg in class_groups}

        # Assign 1 class teacher per class arm per session
        assignments = [
            ("SS 1 Science O", tunde.id),
            ("SS 1 Arts L", grace.id),
            ("JSS 2 O", ibrahim.id),
        ]
        for cg_name, teacher_id in assignments:
            cg = cg_by_name.get(cg_name)
            if cg and cg.class_teacher_id is None:
                cg.class_teacher_id = teacher_id
        await db.flush()

        target_cgs = [
            cg for name in ("SS 1 Science O", "SS 1 Arts L", "JSS 2 O") if (cg := cg_by_name.get(name))
        ]
        student_specs = [
            (
                "OBH/2026/001",
                "Chinedu",
                "Okafor",
                "M",
                "Mr. Emeka Okafor",
                "emeka.okafor@example.com",
                "+2348030001001",
                "SS 1 Science O",
            ),
            (
                "OBH/2026/002",
                "Amina",
                "Yusuf",
                "F",
                "Hajia Fatima Yusuf",
                "fatima.yusuf@example.com",
                "+2348030001002",
                "SS 1 Science O",
            ),
            (
                "OBH/2026/003",
                "David",
                "Akinyemi",
                "M",
                "Mrs. Toyin Akinyemi",
                "toyin.akinyemi@example.com",
                "+2348030001003",
                "SS 1 Science O",
            ),
            (
                "OBH/2026/004",
                "Favour",
                "Eze",
                "F",
                "Mr. Chidi Eze",
                "chidi.eze@example.com",
                "+2348030001004",
                "SS 1 Science O",
            ),
            (
                "OBH/2026/005",
                "Zainab",
                "Musa",
                "F",
                "Alhaji Garba Musa",
                "garba.musa@example.com",
                "+2348030001005",
                "SS 1 Science O",
            ),
            (
                "OBH/2026/006",
                "Toluwani",
                "Adeyinka",
                "M",
                "Dr. Bode Adeyinka",
                "bode.adeyinka@example.com",
                "+2348030001006",
                "SS 1 Arts L",
            ),
            (
                "OBH/2026/007",
                "Chisom",
                "Nwankwo",
                "F",
                "Mrs. Ngozi Nwankwo",
                "ngozi.nwankwo@example.com",
                "+2348030001007",
                "SS 1 Arts L",
            ),
            (
                "OBH/2026/008",
                "Sodiq",
                "Lawal",
                "M",
                "Mr. Rasheed Lawal",
                "rasheed.lawal@example.com",
                "+2348030001008",
                "SS 1 Arts L",
            ),
            (
                "OBH/2026/009",
                "Blessing",
                "Udoh",
                "F",
                "Mr. Etim Udoh",
                "etim.udoh@example.com",
                "+2348030001009",
                "JSS 2 O",
            ),
            (
                "OBH/2026/010",
                "Emmanuel",
                "Olatunji",
                "M",
                "Mrs. Bimbo Olatunji",
                "bimbo.olatunji@example.com",
                "+2348030001010",
                "JSS 2 O",
            ),
            (
                "OBH/2026/011",
                "Mariam",
                "Bello",
                "F",
                "Mr. Sadiq Bello",
                "sadiq.bello@example.com",
                "+2348030001011",
                "JSS 2 O",
            ),
            (
                "OBH/2026/012",
                "Victor",
                "Okonkwo",
                "M",
                "Mrs. Ifeoma Okonkwo",
                "ifeoma.okonkwo@example.com",
                "+2348030001012",
                "JSS 2 O",
            ),
        ]

        enrollments_by_cg: dict[UUID, list[StudentEnrollment]] = {}
        for idx, (adm, fn, ln, gender, g_name, g_email, g_phone, cg_name) in enumerate(
            student_specs
        ):
            cg = cg_by_name.get(cg_name)
            if not cg:
                continue
            student = (
                await db.execute(
                    select(Student).where(
                        Student.school_id == school.id,
                        Student.admission_number == adm,
                    )
                )
            ).scalar_one_or_none()
            if student is None:
                student = Student(
                    school_id=school.id,
                    admission_number=adm,
                    first_name=fn,
                    last_name=ln,
                    date_of_birth=date(2010 + (idx % 3), (idx % 12) + 1, (idx % 25) + 1),
                    gender=gender,
                    guardian_name=g_name,
                    guardian_email=g_email,
                    guardian_phone=g_phone,
                    enrollment_date=today - timedelta(days=40),
                    status="ACTIVE",
                )
                db.add(student)
                await db.flush()

            enrollment = (
                await db.execute(
                    select(StudentEnrollment).where(
                        StudentEnrollment.school_id == school.id,
                        StudentEnrollment.student_id == student.id,
                        StudentEnrollment.academic_session_id == session.id,
                    )
                )
            ).scalar_one_or_none()
            if enrollment is None:
                enrollment = StudentEnrollment(
                    school_id=school.id,
                    student_id=student.id,
                    academic_session_id=session.id,
                    class_group_id=cg.id,
                    enrollment_status="ENROLLED",
                )
                db.add(enrollment)
                await db.flush()
            enrollments_by_cg.setdefault(cg.id, []).append(enrollment)

        # Seed core subjects and class subjects for target classes
        subject_specs = [
            ("MTH", "Mathematics", "CORE", tunde.id),
            ("ENG", "English Language", "CORE", chidinma.id),
            ("CHM", "Chemistry", "CORE", grace.id),
            ("BSC", "Basic Science", "CORE", ibrahim.id),
        ]
        for code, name, cat, teacher_id in subject_specs:
            subj = (
                await db.execute(
                    select(Subject).where(Subject.school_id == school.id, Subject.code == code)
                )
            ).scalar_one_or_none()
            if subj is None:
                subj = Subject(
                    school_id=school.id,
                    code=code,
                    name=name,
                    is_elective=False,
                    is_trade_subject=False,
                )
                db.add(subj)
                await db.flush()

            for cg in target_cgs:
                if code == "BSC" and not cg.display_name.startswith("JSS"):
                    continue
                if code == "CHM" and "Science" not in cg.display_name:
                    continue

                offering = (
                    await db.execute(
                        select(SubjectOffering).where(
                            SubjectOffering.school_id == school.id,
                            SubjectOffering.academic_session_id == session.id,
                            SubjectOffering.academic_level_id == cg.academic_level_id,
                            SubjectOffering.department_id == cg.department_id,
                            SubjectOffering.subject_id == subj.id,
                        )
                    )
                ).scalar_one_or_none()
                if offering is None:
                    offering = SubjectOffering(
                        school_id=school.id,
                        academic_session_id=session.id,
                        academic_level_id=cg.academic_level_id,
                        department_id=cg.department_id,
                        subject_id=subj.id,
                    )
                    db.add(offering)
                    await db.flush()

                cs = (
                    await db.execute(
                        select(ClassSubject).where(
                            ClassSubject.school_id == school.id,
                            ClassSubject.class_group_id == cg.id,
                            ClassSubject.subject_offering_id == offering.id,
                        )
                    )
                ).scalar_one_or_none()
                if cs is None:
                    cs = ClassSubject(
                        school_id=school.id,
                        class_group_id=cg.id,
                        subject_offering_id=offering.id,
                    )
                    db.add(cs)
                    await db.flush()

                ta = (
                    await db.execute(
                        select(TeacherAssignment).where(
                            TeacherAssignment.school_id == school.id,
                            TeacherAssignment.class_subject_id == cs.id,
                            TeacherAssignment.teacher_id == teacher_id,
                        )
                    )
                ).scalar_one_or_none()
                if ta is None:
                    db.add(
                        TeacherAssignment(
                            school_id=school.id,
                            class_subject_id=cs.id,
                            teacher_id=teacher_id,
                            assigned_by_id=admin.id,
                            is_active=True,
                        )
                    )
                    await db.flush()

                # Seed sample 1st Test definition & scores for SS 1 Science O
                if cg.display_name == "SS 1 Science O":
                    defn = (
                        await db.execute(
                            select(AssessmentDefinition).where(
                                AssessmentDefinition.school_id == school.id,
                                AssessmentDefinition.academic_term_id == term.id,
                                AssessmentDefinition.class_subject_id == cs.id,
                                AssessmentDefinition.name == "1st Test",
                            )
                        )
                    ).scalar_one_or_none()
                    if defn is None:
                        defn = AssessmentDefinition(
                            school_id=school.id,
                            academic_term_id=term.id,
                            class_subject_id=cs.id,
                            name="1st Test",
                            code="CA1",
                            assessment_type="CA",
                            max_score=Decimal("10.00"),
                            weight_percentage=Decimal("10.00"),
                            sequence_order=1,
                        )
                        db.add(defn)
                        await db.flush()
                        for s_idx, enr in enumerate(enrollments_by_cg.get(cg.id, [])):
                            db.add(
                                AssessmentScore(
                                    school_id=school.id,
                                    assessment_definition_id=defn.id,
                                    student_enrollment_id=enr.id,
                                    raw_score=Decimal(str(7 + (s_idx % 4))),
                                    score_status="SCORED",
                                    recorded_by_id=teacher_id,
                                    last_modified_by_id=teacher_id,
                                )
                            )

        # Seed today's attendance for SS 1 Science O
        ss1_o = cg_by_name.get("SS 1 Science O")
        if ss1_o:
            for e_idx, enr in enumerate(enrollments_by_cg.get(ss1_o.id, [])):
                existing_att = (
                    await db.execute(
                        select(AttendanceRecord).where(
                            AttendanceRecord.school_id == school.id,
                            AttendanceRecord.academic_term_id == term.id,
                            AttendanceRecord.student_enrollment_id == enr.id,
                            AttendanceRecord.attendance_date == today,
                        )
                    )
                ).scalar_one_or_none()
                if existing_att is None:
                    db.add(
                        AttendanceRecord(
                            school_id=school.id,
                            academic_term_id=term.id,
                            student_enrollment_id=enr.id,
                            attendance_date=today,
                            status="ABSENT" if e_idx == 2 else "PRESENT",
                            recorded_by_id=tunde.id,
                        )
                    )
                existing_sum = (
                    await db.execute(
                        select(AttendanceSummary).where(
                            AttendanceSummary.school_id == school.id,
                            AttendanceSummary.academic_term_id == term.id,
                            AttendanceSummary.student_enrollment_id == enr.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing_sum is None:
                    db.add(
                        AttendanceSummary(
                            school_id=school.id,
                            academic_term_id=term.id,
                            student_enrollment_id=enr.id,
                            times_school_opened=1,
                            times_present=0 if e_idx == 2 else 1,
                            times_absent=1 if e_idx == 2 else 0,
                            source="DAILY",
                        )
                    )

        await db.commit()
        print(
            "Seeded Olivet Baptist High School demo data successfully!\n"
            f"  Admin:            {admin.email} / {DEMO_PASSWORD}\n"
            f"  Academic Officer: {officer.email} / {DEMO_PASSWORD}\n"
            f"  Class Teacher:    {tunde.email} / {DEMO_PASSWORD} (SS 1 Science O)\n"
            f"  Class Teacher:    {grace.email} / {DEMO_PASSWORD} (SS 1 Arts L)\n"
            f"  Class Teacher:    {ibrahim.email} / {DEMO_PASSWORD} (JSS 2 O)\n"
            f"  Subject Teacher:  {chidinma.email} / {DEMO_PASSWORD}"
        )


if __name__ == "__main__":
    asyncio.run(seed_demo())
