"""Realistic Nigerian Senior Secondary (SS1-SS3) demo seed script for Olivet Baptist High School, Oyo.

Usage:
    .venv/bin/python -m scripts.seed_demo           # idempotent seed
    .venv/bin/python -m scripts.seed_demo --reset   # wipe OBHS-OYO and reseed afresh
"""

import argparse
import asyncio
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

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

DEMO_PASSWORD = "OlivetDemo123!"

MALE_FIRST_NAMES = [
    "Chinedu",
    "Tunde",
    "David",
    "Toluwani",
    "Sodiq",
    "Emmanuel",
    "Victor",
    "Ibrahim",
    "Olumide",
    "Kelechi",
    "Abubakar",
    "Femi",
    "Chukwuemeka",
    "Damilola",
    "Yusuf",
    "Samuel",
    "Babajide",
    "Nnamdi",
    "Musa",
    "Ayomide",
    "Oluwaseun",
    "Uche",
    "Sadiq",
    "Adebayo",
    "Jide",
    "Ifeanyi",
    "Usman",
    "Kehinde",
    "Taiwo",
    "Chibuzo",
    "Gbolahan",
    "Bamidele",
    "Abdulrahman",
    "Efe",
    "Osagie",
    "Kolawole",
    "Oluseyi",
    "Tochukwu",
    "Idris",
    "Segun",
]

FEMALE_FIRST_NAMES = [
    "Amina",
    "Favour",
    "Zainab",
    "Chisom",
    "Blessing",
    "Mariam",
    "Ngozi",
    "Temitope",
    "Halima",
    "Adaeze",
    "Bukola",
    "Kemi",
    "Chidinma",
    "Rukayat",
    "Esther",
    "Oluwatobi",
    "Sadiya",
    "Ifeoma",
    "Titilayo",
    "Amarachi",
    "Folashade",
    "Hauwa",
    "Yetunde",
    "Nneka",
    "Aishat",
    "Simisola",
    "Omolara",
    "Chinyere",
    "Fatima",
    "Bisola",
    "Uju",
    "Maimuna",
    "Tolani",
    "Ejiro",
    "Iniobong",
    "Olubunmi",
    "Khadijat",
    "Precious",
    "Doyin",
    "Funke",
]

LAST_NAMES = [
    "Okafor",
    "Yusuf",
    "Akinyemi",
    "Eze",
    "Musa",
    "Adeyinka",
    "Nwankwo",
    "Lawal",
    "Udoh",
    "Olatunji",
    "Bello",
    "Okonkwo",
    "Balogun",
    "Adebisi",
    "Ojo",
    "Ibe",
    "Mohammed",
    "Ogundipe",
    "Chukwu",
    "Salami",
    "Oni",
    "Danladi",
    "Oladipo",
    "Nwachukwu",
    "Fashola",
    "Garba",
    "Bankole",
    "Osuji",
    "Bakare",
    "Okeke",
    "Alabi",
    "Suleiman",
    "Afolabi",
    "Agboola",
    "Onyeka",
    "Sanusi",
    "Akinola",
    "Obasanjo",
    "Adekunle",
    "Igwe",
    "Abdullahi",
    "Ogunleye",
    "Nwafor",
    "Adetola",
    "Yakubu",
    "Ezekiel",
    "Ajayi",
    "Ogunbiyi",
]

GUARDIAN_TITLES_M = ["Mr.", "Alhaji", "Dr.", "Chief", "Engr.", "Pastor"]
GUARDIAN_TITLES_F = ["Mrs.", "Hajia", "Dr.", "Chief Mrs.", "Deaconess"]


async def _set_tenant_context(db: AsyncSession, school_id: UUID) -> None:
    _tenant_school_id.set(str(school_id))
    await db.execute(
        text("SELECT set_config('app.current_school_id', :sid, false)"),
        {"sid": str(school_id)},
    )


async def _reset_pilot_school(db: AsyncSession) -> None:
    await db.execute(text("SET LOCAL app.is_super_admin = 'true'"))
    school = (
        await db.execute(select(School).where(School.official_code == "OBHS-OYO"))
    ).scalar_one_or_none()
    if school is None:
        return

    sid = school.id
    # Delete in reverse foreign-key dependency order
    for model in [
        ResultAccessToken,
        SubjectResult,
        TermReportCard,
        AssessmentScore,
        ScoreSheetStatus,
        AssessmentDefinition,
        AttendanceRecord,
        AttendanceSummary,
        StudentSubjectRegistration,
        TeacherAssignment,
        ClassSubject,
        SubjectOffering,
        StudentEnrollment,
        Student,
        ClassGroup,
        AcademicTerm,
        AcademicSession,
        ClassArm,
        Department,
        AcademicLevel,
        Subject,
        UserPermissionGrant,
        SchoolRolePermission,
        AuditLog,
        StaffProfile,
        User,
        SchoolConfiguration,
    ]:
        await db.execute(delete(model).where(model.school_id == sid))

    await db.execute(delete(School).where(School.id == sid))
    await db.commit()
    print("Reset existing Olivet Baptist High School (OBHS-OYO) data.")


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
            show_department_position_on_report_card=False,
            show_level_position_on_report_card=False,
            show_subject_position_on_report_card=False,
            setup_wizard_completed=True,
            setup_wizard_dismissed=False,
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


async def seed_demo(*, reset: bool = False) -> None:
    rng = random.Random(20261123)

    if reset:
        async with async_session_factory() as db:
            await _reset_pilot_school(db)

    async with async_session_factory() as db:
        await seed_system_permissions(db)
        school = await _get_or_create_school(db)
        await _set_tenant_context(db, school.id)

        # ------------------------------------------------------------------
        # 1. Staff Accounts (preserving original login emails + password)
        # ------------------------------------------------------------------
        staff_specs = [
            (
                "folake.adebayo@olivet.edu.ng",
                "Folake",
                "Adebayo",
                "+2348034451092",
                "SCHOOL_ADMIN",
                "OBHS/STF/001",
                "School Administration",
                "ACTIVE",
            ),
            (
                "kunle.adeyemi@olivet.edu.ng",
                "Kunle",
                "Adeyemi",
                "+2348031122334",
                "ACADEMIC_OFFICER",
                "OBHS/STF/002",
                "Academic Records",
                "ACTIVE",
            ),
            (
                "tunde.balogun@olivet.edu.ng",
                "Tunde",
                "Balogun",
                "+2348023344556",
                "TEACHER",
                "OBHS/STF/003",
                "Mathematics",
                "ACTIVE",
            ),
            (
                "grace.ojo@olivet.edu.ng",
                "Grace",
                "Ojo",
                "+2348056677889",
                "TEACHER",
                "OBHS/STF/004",
                "Chemistry",
                "ACTIVE",
            ),
            (
                "ibrahim.musa@olivet.edu.ng",
                "Ibrahim",
                "Musa",
                "+2348067788990",
                "TEACHER",
                "OBHS/STF/005",
                "Physics",
                "ACTIVE",
            ),
            (
                "chidinma.nwosu@olivet.edu.ng",
                "Chidinma",
                "Nwosu",
                "+2348078899001",
                "TEACHER",
                "OBHS/STF/006",
                "English Language",
                "ACTIVE",
            ),
            (
                "aisha.adamu@olivet.edu.ng",
                "Aisha",
                "Adamu",
                "+2348091122334",
                "TEACHER",
                "OBHS/STF/007",
                "Biology",
                "ACTIVE",
            ),
            (
                "emeka.eze@olivet.edu.ng",
                "Emeka",
                "Eze",
                "+2348035566771",
                "TEACHER",
                "OBHS/STF/008",
                "Civic Education & Government",
                "ACTIVE",
            ),
            (
                "olumide.adekunle@olivet.edu.ng",
                "Olumide",
                "Adekunle",
                "+2348035566772",
                "TEACHER",
                "OBHS/STF/009",
                "Literature & History",
                "ACTIVE",
            ),
            (
                "bukola.olatunji@olivet.edu.ng",
                "Bukola",
                "Olatunji",
                "+2348035566773",
                "TEACHER",
                "OBHS/STF/010",
                "Financial Accounting & Commerce",
                "ACTIVE",
            ),
            (
                "sadiq.bello@olivet.edu.ng",
                "Sadiq",
                "Bello",
                "+2348035566774",
                "TEACHER",
                "OBHS/STF/011",
                "Economics & Marketing",
                "ACTIVE",
            ),
            (
                "ngozi.okonkwo@olivet.edu.ng",
                "Ngozi",
                "Okonkwo",
                "+2348035566775",
                "TEACHER",
                "OBHS/STF/012",
                "Data Processing & ICT",
                "ACTIVE",
            ),
            (
                "kehinde.alabi@olivet.edu.ng",
                "Kehinde",
                "Alabi",
                "+2348035566776",
                "TEACHER",
                "OBHS/STF/013",
                "Agricultural Science & Fisheries",
                "ACTIVE",
            ),
            (
                "funke.ajayi@olivet.edu.ng",
                "Funke",
                "Ajayi",
                "+2348035566777",
                "TEACHER",
                "OBHS/STF/014",
                "Catering Craft & Garment Making",
                "ACTIVE",
            ),
            (
                "usman.garba@olivet.edu.ng",
                "Usman",
                "Garba",
                "+2348035566778",
                "TEACHER",
                "OBHS/STF/015",
                "Religious Studies & Yoruba",
                "ACTIVE",
            ),
            (
                "segun.bankole@olivet.edu.ng",
                "Segun",
                "Bankole",
                "+2348035566779",
                "TEACHER",
                "OBHS/STF/016",
                "Further Mathematics & Technical Drawing",
                "ACTIVE",
            ),
        ]

        staff_by_email: dict[str, User] = {}
        for email, fn, ln, phone, role, staff_id, spec, status in staff_specs:
            staff_by_email[email] = await _get_or_create_staff(
                db,
                school.id,
                email=email,
                first_name=fn,
                last_name=ln,
                phone=phone,
                role=role,
                staff_id_number=staff_id,
                specialization=spec,
                employment_status=status,
            )

        admin = staff_by_email["folake.adebayo@olivet.edu.ng"]
        teachers = [u for u in staff_by_email.values() if u.role == "TEACHER"]

        # ------------------------------------------------------------------
        # 2. Session, Terms, SS1-SS3 Levels, Departments & Arms
        # ------------------------------------------------------------------
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
            session = AcademicSession(
                school_id=school.id,
                name="2026/2027",
                start_date=today - timedelta(days=45),
                end_date=today + timedelta(days=270),
                is_current=True,
            )
            db.add(session)
            await db.flush()

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
            term = AcademicTerm(
                school_id=school.id,
                academic_session_id=session.id,
                name="First Term",
                term_order=1,
                start_date=today - timedelta(days=45),
                end_date=today + timedelta(days=45),
                is_current=True,
                is_locked_for_scoring=False,
            )
            db.add(term)
            await db.flush()

        await _set_tenant_context(db, school.id)

        # Levels: SS 1, SS 2, SS 3
        level_specs = [
            ("SS 1", "SS1", 1, "SENIOR_SECONDARY"),
            ("SS 2", "SS2", 2, "SENIOR_SECONDARY"),
            ("SS 3", "SS3", 3, "SENIOR_SECONDARY"),
        ]
        levels_by_code: dict[str, AcademicLevel] = {}
        for name, code, order, cat in level_specs:
            lvl = (
                await db.execute(
                    select(AcademicLevel).where(
                        AcademicLevel.school_id == school.id,
                        AcademicLevel.code == code,
                        AcademicLevel.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if lvl is None:
                lvl = AcademicLevel(
                    school_id=school.id,
                    name=name,
                    code=code,
                    level_order=order,
                    category=cat,
                )
                db.add(lvl)
                await db.flush()
            levels_by_code[code] = lvl

        # Departments: Science, Arts, Commercial
        dept_specs = [
            ("Science", "SCI"),
            ("Arts", "ART"),
            ("Commercial", "COMM"),
        ]
        depts_by_code: dict[str, Department] = {}
        for name, code in dept_specs:
            dept = (
                await db.execute(
                    select(Department).where(
                        Department.school_id == school.id,
                        Department.code == code,
                        Department.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if dept is None:
                dept = Department(school_id=school.id, name=name, code=code)
                db.add(dept)
                await db.flush()
            depts_by_code[code] = dept

        # Arms: O, L, I, V (Science), E (Commercial), T, B (Arts)
        arm_letters = ["O", "L", "I", "V", "E", "T", "B"]
        arms_by_name: dict[str, ClassArm] = {}
        for arm_name in arm_letters:
            arm = (
                await db.execute(
                    select(ClassArm).where(
                        ClassArm.school_id == school.id,
                        ClassArm.name == arm_name,
                        ClassArm.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if arm is None:
                arm = ClassArm(school_id=school.id, name=arm_name)
                db.add(arm)
                await db.flush()
            arms_by_name[arm_name] = arm

        # Realistic SS1-SS3 ClassGroup distribution:
        # Science -> O, L, I | Commercial -> E, V | Arts -> T, B (21 class arms total)
        dept_arm_map = {
            "SCI": ["O", "L", "I"],
            "COMM": ["E", "V"],
            "ART": ["T", "B"],
        }
        class_groups: list[ClassGroup] = []
        cg_idx = 0
        for _lvl_code, lvl in levels_by_code.items():
            for dept_code, arm_list in dept_arm_map.items():
                dept = depts_by_code[dept_code]
                for arm_letter in arm_list:
                    arm = arms_by_name[arm_letter]
                    display_name = f"{lvl.name} {dept.name} {arm.name}"
                    cg = (
                        await db.execute(
                            select(ClassGroup).where(
                                ClassGroup.school_id == school.id,
                                ClassGroup.academic_session_id == session.id,
                                ClassGroup.academic_level_id == lvl.id,
                                ClassGroup.department_id == dept.id,
                                ClassGroup.class_arm_id == arm.id,
                                ClassGroup.deleted_at.is_(None),
                            )
                        )
                    ).scalar_one_or_none()
                    if cg is None:
                        cg = ClassGroup(
                            school_id=school.id,
                            academic_session_id=session.id,
                            academic_level_id=lvl.id,
                            department_id=dept.id,
                            class_arm_id=arm.id,
                            class_teacher_id=teachers[cg_idx % len(teachers)].id,
                            display_name=display_name,
                        )
                        db.add(cg)
                        await db.flush()
                    class_groups.append(cg)
                    cg_idx += 1

        # ------------------------------------------------------------------
        # 3. Nigerian NERDC SS1-SS3 Subjects (General, Dept Core, Electives, Trade)
        # ------------------------------------------------------------------
        # (code, name, is_elective, is_trade_subject, teacher_email, departments)
        subject_catalog = [
            # General Compulsory Core (All Departments)
            (
                "ENG",
                "English Language",
                False,
                False,
                "chidinma.nwosu@olivet.edu.ng",
                ["SCI", "ART", "COMM"],
            ),
            (
                "MTH",
                "Mathematics",
                False,
                False,
                "tunde.balogun@olivet.edu.ng",
                ["SCI", "ART", "COMM"],
            ),
            (
                "CIV",
                "Civic Education",
                False,
                False,
                "emeka.eze@olivet.edu.ng",
                ["SCI", "ART", "COMM"],
            ),
            # Science Core
            ("PHY", "Physics", False, False, "ibrahim.musa@olivet.edu.ng", ["SCI"]),
            ("CHM", "Chemistry", False, False, "grace.ojo@olivet.edu.ng", ["SCI"]),
            ("BIO", "Biology", False, False, "aisha.adamu@olivet.edu.ng", ["SCI"]),
            # Science Electives
            ("FMA", "Further Mathematics", True, False, "segun.bankole@olivet.edu.ng", ["SCI"]),
            ("AGR", "Agricultural Science", True, False, "kehinde.alabi@olivet.edu.ng", ["SCI"]),
            ("GEO", "Geography", True, False, "ibrahim.musa@olivet.edu.ng", ["SCI", "ART"]),
            ("TDW", "Technical Drawing", True, False, "segun.bankole@olivet.edu.ng", ["SCI"]),
            # Arts Core
            (
                "LIT",
                "Literature-in-English",
                False,
                False,
                "olumide.adekunle@olivet.edu.ng",
                ["ART"],
            ),
            ("GOV", "Government", False, False, "emeka.eze@olivet.edu.ng", ["ART", "COMM"]),
            ("HIS", "History", False, False, "olumide.adekunle@olivet.edu.ng", ["ART"]),
            # Arts Electives
            (
                "CRS",
                "Christian Religious Studies",
                True,
                False,
                "usman.garba@olivet.edu.ng",
                ["ART"],
            ),
            ("IRS", "Islamic Religious Studies", True, False, "usman.garba@olivet.edu.ng", ["ART"]),
            ("YOR", "Yoruba Language", True, False, "usman.garba@olivet.edu.ng", ["ART"]),
            # Commercial Core
            (
                "ACC",
                "Financial Accounting",
                False,
                False,
                "bukola.olatunji@olivet.edu.ng",
                ["COMM"],
            ),
            ("COM", "Commerce", False, False, "bukola.olatunji@olivet.edu.ng", ["COMM"]),
            ("ECO", "Economics", False, False, "sadiq.bello@olivet.edu.ng", ["COMM", "ART"]),
            # Commercial Electives
            ("BKM", "Book Keeping", True, False, "bukola.olatunji@olivet.edu.ng", ["COMM"]),
            ("OFP", "Office Practice", True, False, "sadiq.bello@olivet.edu.ng", ["COMM"]),
            # Trade / Entrepreneurship Subjects (All Departments)
            (
                "DPR",
                "Data Processing",
                True,
                True,
                "ngozi.okonkwo@olivet.edu.ng",
                ["SCI", "ART", "COMM"],
            ),
            (
                "CAT",
                "Catering Craft Practice",
                True,
                True,
                "funke.ajayi@olivet.edu.ng",
                ["SCI", "ART", "COMM"],
            ),
            ("GAR", "Garment Making", True, True, "funke.ajayi@olivet.edu.ng", ["ART", "COMM"]),
            ("FIS", "Fisheries", True, True, "kehinde.alabi@olivet.edu.ng", ["SCI", "COMM"]),
            ("MKT", "Marketing", True, True, "sadiq.bello@olivet.edu.ng", ["SCI", "ART", "COMM"]),
        ]

        subjects_by_code: dict[str, Subject] = {}
        subject_teacher_by_code: dict[str, UUID] = {}
        subject_depts_by_code: dict[str, list[str]] = {}
        for code, name, is_elec, is_trade, t_email, depts in subject_catalog:
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
                    is_elective=is_elec,
                    is_trade_subject=is_trade,
                )
                db.add(subj)
                await db.flush()
            subjects_by_code[code] = subj
            subject_teacher_by_code[code] = staff_by_email[t_email].id
            subject_depts_by_code[code] = depts

        # Create SubjectOfferings + ClassSubjects + TeacherAssignments + 4 AssessmentDefinitions (10/10/10/70)
        dept_code_by_id = {d.id: code for code, d in depts_by_code.items()}
        cs_by_cg_and_code: dict[tuple[UUID, str], ClassSubject] = {}
        defs_by_cs_id: dict[UUID, list[AssessmentDefinition]] = {}

        default_components = [
            ("Test 1", "TEST1", "CONTINUOUS_ASSESSMENT", Decimal("10.00"), Decimal("10.00"), 1),
            ("Test 2", "TEST2", "CONTINUOUS_ASSESSMENT", Decimal("10.00"), Decimal("10.00"), 2),
            (
                "Assignments/Attendance",
                "ASSIGN",
                "CONTINUOUS_ASSESSMENT",
                Decimal("10.00"),
                Decimal("10.00"),
                3,
            ),
            ("Terminal Examination", "EXAM", "EXAMINATION", Decimal("70.00"), Decimal("70.00"), 4),
        ]

        offerings_cache: dict[tuple[UUID, UUID, UUID], SubjectOffering] = {}
        for cg in class_groups:
            d_code = dept_code_by_id[cg.department_id]
            for s_code, subj in subjects_by_code.items():
                if d_code not in subject_depts_by_code[s_code]:
                    continue

                off_key = (cg.academic_level_id, cg.department_id, subj.id)
                offering = offerings_cache.get(off_key)
                if offering is None:
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
                    offerings_cache[off_key] = offering

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
                cs_by_cg_and_code[(cg.id, s_code)] = cs

                t_id = subject_teacher_by_code[s_code]
                ta = (
                    await db.execute(
                        select(TeacherAssignment).where(
                            TeacherAssignment.school_id == school.id,
                            TeacherAssignment.class_subject_id == cs.id,
                            TeacherAssignment.teacher_id == t_id,
                        )
                    )
                ).scalar_one_or_none()
                if ta is None:
                    db.add(
                        TeacherAssignment(
                            school_id=school.id,
                            class_subject_id=cs.id,
                            teacher_id=t_id,
                            assigned_by_id=admin.id,
                            is_active=True,
                        )
                    )

                existing_defs = list(
                    (
                        await db.execute(
                            select(AssessmentDefinition).where(
                                AssessmentDefinition.school_id == school.id,
                                AssessmentDefinition.academic_term_id == term.id,
                                AssessmentDefinition.class_subject_id == cs.id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if not existing_defs:
                    for c_name, c_code, c_type, c_max, c_weight, c_seq in default_components:
                        d = AssessmentDefinition(
                            school_id=school.id,
                            academic_term_id=term.id,
                            class_subject_id=cs.id,
                            name=c_name,
                            code=c_code,
                            assessment_type=c_type,
                            max_score=c_max,
                            weight_percentage=c_weight,
                            sequence_order=c_seq,
                        )
                        db.add(d)
                        existing_defs.append(d)
                defs_by_cs_id[cs.id] = existing_defs

        await db.flush()

        # ------------------------------------------------------------------
        # 4. Students (15-20 per arm), Enrollments, Subject Registrations & Scores
        # ------------------------------------------------------------------
        # Subject selection templates per department
        dept_core_codes = {
            "SCI": ["ENG", "MTH", "CIV", "PHY", "CHM", "BIO"],
            "ART": ["ENG", "MTH", "CIV", "LIT", "GOV", "HIS"],
            "COMM": ["ENG", "MTH", "CIV", "ACC", "COM", "ECO"],
        }
        dept_elective_pools = {
            "SCI": [["FMA", "AGR"], ["AGR", "GEO"], ["FMA", "TDW"], ["GEO"]],
            "ART": [["CRS", "ECO"], ["IRS", "YOR"], ["CRS", "GEO"], ["YOR", "ECO"]],
            "COMM": [["GOV", "BKM"], ["OFP"], ["BKM", "OFP"], ["GOV"]],
        }
        trade_pool_by_dept = {
            "SCI": ["DPR", "FIS", "MKT", "CAT"],
            "ART": ["CAT", "GAR", "DPR", "MKT"],
            "COMM": ["MKT", "DPR", "CAT", "FIS"],
        }

        level_birth_year = {
            levels_by_code["SS1"].id: 2010,
            levels_by_code["SS2"].id: 2009,
            levels_by_code["SS3"].id: 2008,
        }

        adm_counter = 1
        total_students = 0
        now_utc = datetime.now(UTC)

        for cg in class_groups:
            d_code = dept_code_by_id[cg.department_id]
            arm_size = rng.randint(15, 20)
            base_year = level_birth_year[cg.academic_level_id]

            for s_idx in range(arm_size):
                adm_num = f"OBHS/2026/{adm_counter:03d}"
                adm_counter += 1
                total_students += 1

                is_male = rng.choice([True, False])
                fn = rng.choice(MALE_FIRST_NAMES if is_male else FEMALE_FIRST_NAMES)
                ln = rng.choice(LAST_NAMES)
                g_title = rng.choice(GUARDIAN_TITLES_M if s_idx % 2 == 0 else GUARDIAN_TITLES_F)
                g_fn = rng.choice(MALE_FIRST_NAMES if s_idx % 2 == 0 else FEMALE_FIRST_NAMES)
                g_name = f"{g_title} {g_fn} {ln}"
                g_email = f"{g_fn.lower()}.{ln.lower()}.{adm_counter}@example.com"
                g_phone = f"+23480{rng.randint(2, 9)}{adm_counter:07d}"

                dob = date(base_year, rng.randint(1, 12), rng.randint(1, 28))

                student = (
                    await db.execute(
                        select(Student).where(
                            Student.school_id == school.id,
                            Student.admission_number == adm_num,
                        )
                    )
                ).scalar_one_or_none()
                if student is None:
                    student = Student(
                        school_id=school.id,
                        admission_number=adm_num,
                        first_name=fn,
                        last_name=ln,
                        date_of_birth=dob,
                        gender="M" if is_male else "F",
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

                # Choose student's subjects: Core + Dept Electives + 1 Trade Subject
                chosen_codes = list(dept_core_codes[d_code])
                chosen_codes.extend(rng.choice(dept_elective_pools[d_code]))
                chosen_codes.append(rng.choice(trade_pool_by_dept[d_code]))

                # Give each student a realistic ability baseline so rankings spread nicely
                student_ability = rng.uniform(0.45, 0.92)

                for code in chosen_codes:
                    cs = cs_by_cg_and_code.get((cg.id, code))
                    if cs is None:
                        continue

                    reg = (
                        await db.execute(
                            select(StudentSubjectRegistration).where(
                                StudentSubjectRegistration.school_id == school.id,
                                StudentSubjectRegistration.student_enrollment_id == enrollment.id,
                                StudentSubjectRegistration.class_subject_id == cs.id,
                            )
                        )
                    ).scalar_one_or_none()
                    if reg is None:
                        db.add(
                            StudentSubjectRegistration(
                                school_id=school.id,
                                student_enrollment_id=enrollment.id,
                                class_subject_id=cs.id,
                                is_active=True,
                            )
                        )

                    # Seed realistic scores for each component
                    t_id = subject_teacher_by_code[code]
                    for defn in defs_by_cs_id.get(cs.id, []):
                        existing_score = (
                            await db.execute(
                                select(AssessmentScore.id).where(
                                    AssessmentScore.school_id == school.id,
                                    AssessmentScore.assessment_definition_id == defn.id,
                                    AssessmentScore.student_enrollment_id == enrollment.id,
                                )
                            )
                        ).scalar_one_or_none()
                        if existing_score is not None:
                            continue
                        max_val = float(defn.max_score)
                        raw_val = round(
                            max(
                                2.0,
                                min(
                                    max_val, max_val * (student_ability + rng.uniform(-0.12, 0.10))
                                ),
                            ),
                            1,
                        )
                        db.add(
                            AssessmentScore(
                                school_id=school.id,
                                assessment_definition_id=defn.id,
                                student_enrollment_id=enrollment.id,
                                raw_score=Decimal(str(raw_val)),
                                score_status="SCORED",
                                recorded_by_id=t_id,
                                last_modified_by_id=t_id,
                            )
                        )

        # Mark all ClassSubject score sheets as APPROVED so Results & Broadsheets can be tested right away
        for (_, _), cs in cs_by_cg_and_code.items():
            existing_status = (
                await db.execute(
                    select(ScoreSheetStatus).where(
                        ScoreSheetStatus.school_id == school.id,
                        ScoreSheetStatus.academic_term_id == term.id,
                        ScoreSheetStatus.class_subject_id == cs.id,
                    )
                )
            ).scalar_one_or_none()
            if existing_status is None:
                db.add(
                    ScoreSheetStatus(
                        school_id=school.id,
                        academic_term_id=term.id,
                        class_subject_id=cs.id,
                        status="APPROVED",
                        submitted_by_id=admin.id,
                        submitted_at=now_utc - timedelta(days=2),
                        approved_by_id=admin.id,
                        approved_at=now_utc - timedelta(days=1),
                    )
                )

        await db.commit()
        print(
            f"Seeded {school.name}: 3 levels (SS 1-SS 3), {len(class_groups)} class arms, "
            f"{len(subjects_by_code)} subjects, {len(staff_by_email)} staff, {total_students} enrolled students."
        )
        print(f"Login password for all accounts: {DEMO_PASSWORD}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Olivet Baptist High School demo data.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe existing OBHS-OYO school data before seeding.",
    )
    args = parser.parse_args()
    asyncio.run(seed_demo(reset=args.reset))


if __name__ == "__main__":
    main()
