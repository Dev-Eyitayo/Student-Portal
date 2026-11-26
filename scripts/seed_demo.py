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
from app.modules.identity.schemas import SchoolSector, SchoolType
from app.modules.identity.seed import seed_system_permissions
from app.modules.identity.services import get_school_defaults
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
        # PUBLIC so SchoolConfiguration below picks up the TESCOM assessment
        # weight schema (ATTENDANCE/NOTEBOOK/OPEN_DAY/TEXTBOOK/SB_TEST/
        # TB_ASSESS + EXAM) that the Mark Book export is built against.
        school_sector=SchoolSector.PUBLIC.value,
        is_active=True,
    )
    db.add(school)
    await db.flush()

    defaults = get_school_defaults(SchoolType.SECONDARY, SchoolSector.PUBLIC)
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
        print("Seeding academic levels, departments, arms, and class groups...", flush=True)

        # Levels: SS 1, SS 2, SS 3
        level_specs = [
            ("SS 1", "SS1", 1, "SENIOR_SECONDARY"),
            ("SS 2", "SS2", 2, "SENIOR_SECONDARY"),
            ("SS 3", "SS3", 3, "SENIOR_SECONDARY"),
        ]
        existing_levels = (
            (
                await db.execute(
                    select(AcademicLevel).where(
                        AcademicLevel.school_id == school.id,
                        AcademicLevel.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        levels_by_code: dict[str, AcademicLevel] = {lvl.code: lvl for lvl in existing_levels}
        new_levels: list[AcademicLevel] = []
        for name, code, order, cat in level_specs:
            if code not in levels_by_code:
                lvl = AcademicLevel(
                    school_id=school.id,
                    name=name,
                    code=code,
                    level_order=order,
                    category=cat,
                )
                new_levels.append(lvl)
                levels_by_code[code] = lvl
        if new_levels:
            db.add_all(new_levels)

        # Departments: Science, Arts, Commercial
        dept_specs = [
            ("Science", "SCI"),
            ("Arts", "ART"),
            ("Commercial", "COMM"),
        ]
        existing_depts = (
            (
                await db.execute(
                    select(Department).where(
                        Department.school_id == school.id,
                        Department.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        depts_by_code: dict[str, Department] = {d.code: d for d in existing_depts}
        new_depts: list[Department] = []
        for name, code in dept_specs:
            if code not in depts_by_code:
                dept = Department(school_id=school.id, name=name, code=code)
                new_depts.append(dept)
                depts_by_code[code] = dept
        if new_depts:
            db.add_all(new_depts)

        # Arms: O, L, I, V (Science), E (Commercial), T, B (Arts)
        arm_letters = ["O", "L", "I", "V", "E", "T", "B"]
        existing_arms = (
            (
                await db.execute(
                    select(ClassArm).where(
                        ClassArm.school_id == school.id,
                        ClassArm.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        arms_by_name: dict[str, ClassArm] = {a.name: a for a in existing_arms}
        new_arms: list[ClassArm] = []
        for arm_name in arm_letters:
            if arm_name not in arms_by_name:
                arm = ClassArm(school_id=school.id, name=arm_name)
                new_arms.append(arm)
                arms_by_name[arm_name] = arm
        if new_arms:
            db.add_all(new_arms)

        if new_levels or new_depts or new_arms:
            await db.flush()

        # Realistic SS1-SS3 ClassGroup distribution:
        # Science -> O, L, I | Commercial -> E, V | Arts -> T, B (21 class arms total)
        dept_arm_map = {
            "SCI": ["O", "L", "I"],
            "COMM": ["E", "V"],
            "ART": ["T", "B"],
        }
        existing_cgs = (
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
        cgs_by_key = {
            (cg.academic_level_id, cg.department_id, cg.class_arm_id): cg for cg in existing_cgs
        }
        class_groups: list[ClassGroup] = []
        new_cgs: list[ClassGroup] = []
        cg_idx = 0
        for lvl_code in ("SS1", "SS2", "SS3"):
            lvl = levels_by_code[lvl_code]
            for dept_code, arm_list in dept_arm_map.items():
                dept = depts_by_code[dept_code]
                for arm_letter in arm_list:
                    arm = arms_by_name[arm_letter]
                    key = (lvl.id, dept.id, arm.id)
                    cg = cgs_by_key.get(key)
                    if cg is None:
                        cg = ClassGroup(
                            school_id=school.id,
                            academic_session_id=session.id,
                            academic_level_id=lvl.id,
                            department_id=dept.id,
                            class_arm_id=arm.id,
                            class_teacher_id=teachers[cg_idx % len(teachers)].id,
                            display_name=f"{lvl.name} {dept.name} {arm.name}",
                        )
                        new_cgs.append(cg)
                        cgs_by_key[key] = cg
                    class_groups.append(cg)
                    cg_idx += 1
        if new_cgs:
            db.add_all(new_cgs)
            await db.flush()

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

        print("Seeding subjects, offerings, class subjects, and assessments...", flush=True)
        existing_subjects = (
            (await db.execute(select(Subject).where(Subject.school_id == school.id)))
            .scalars()
            .all()
        )
        subjects_by_code: dict[str, Subject] = {s.code: s for s in existing_subjects}
        subject_teacher_by_code: dict[str, UUID] = {}
        subject_depts_by_code: dict[str, list[str]] = {}
        new_subjects: list[Subject] = []
        for code, name, is_elec, is_trade, t_email, depts in subject_catalog:
            if code not in subjects_by_code:
                subj = Subject(
                    school_id=school.id,
                    code=code,
                    name=name,
                    is_elective=is_elec,
                    is_trade_subject=is_trade,
                )
                new_subjects.append(subj)
                subjects_by_code[code] = subj
            subject_teacher_by_code[code] = staff_by_email[t_email].id
            subject_depts_by_code[code] = depts
        if new_subjects:
            db.add_all(new_subjects)
            await db.flush()

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

        existing_offerings = (
            (
                await db.execute(
                    select(SubjectOffering).where(
                        SubjectOffering.school_id == school.id,
                        SubjectOffering.academic_session_id == session.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        offerings_cache: dict[tuple[UUID, UUID | None, UUID], SubjectOffering] = {
            (o.academic_level_id, o.department_id, o.subject_id): o for o in existing_offerings
        }
        new_offerings: list[SubjectOffering] = []
        for cg in class_groups:
            d_code = dept_code_by_id[cg.department_id]
            for s_code, subj in subjects_by_code.items():
                if d_code not in subject_depts_by_code.get(s_code, []):
                    continue
                off_key = (cg.academic_level_id, cg.department_id, subj.id)
                if off_key not in offerings_cache:
                    offering = SubjectOffering(
                        school_id=school.id,
                        academic_session_id=session.id,
                        academic_level_id=cg.academic_level_id,
                        department_id=cg.department_id,
                        subject_id=subj.id,
                    )
                    new_offerings.append(offering)
                    offerings_cache[off_key] = offering
        if new_offerings:
            db.add_all(new_offerings)
            await db.flush()

        existing_class_subjects = (
            (await db.execute(select(ClassSubject).where(ClassSubject.school_id == school.id)))
            .scalars()
            .all()
        )
        cs_cache: dict[tuple[UUID, UUID], ClassSubject] = {
            (cs.class_group_id, cs.subject_offering_id): cs for cs in existing_class_subjects
        }
        new_class_subjects: list[ClassSubject] = []
        cs_entries: list[tuple[ClassSubject, str]] = []
        for cg in class_groups:
            d_code = dept_code_by_id[cg.department_id]
            for s_code, subj in subjects_by_code.items():
                if d_code not in subject_depts_by_code.get(s_code, []):
                    continue
                offering = offerings_cache[(cg.academic_level_id, cg.department_id, subj.id)]
                cs_key = (cg.id, offering.id)
                cs = cs_cache.get(cs_key)
                if cs is None:
                    cs = ClassSubject(
                        school_id=school.id,
                        class_group_id=cg.id,
                        subject_offering_id=offering.id,
                    )
                    new_class_subjects.append(cs)
                    cs_cache[cs_key] = cs
                cs_by_cg_and_code[(cg.id, s_code)] = cs
                cs_entries.append((cs, s_code))
        if new_class_subjects:
            db.add_all(new_class_subjects)
            await db.flush()

        existing_tas = (
            (
                await db.execute(
                    select(TeacherAssignment).where(TeacherAssignment.school_id == school.id)
                )
            )
            .scalars()
            .all()
        )
        ta_keys = {(ta.class_subject_id, ta.teacher_id) for ta in existing_tas}

        existing_all_defs = (
            (
                await db.execute(
                    select(AssessmentDefinition).where(
                        AssessmentDefinition.school_id == school.id,
                        AssessmentDefinition.academic_term_id == term.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for defn in existing_all_defs:
            defs_by_cs_id.setdefault(defn.class_subject_id, []).append(defn)

        new_tas: list[TeacherAssignment] = []
        new_defs: list[AssessmentDefinition] = []
        for cs, s_code in cs_entries:
            t_id = subject_teacher_by_code[s_code]
            if (cs.id, t_id) not in ta_keys:
                new_tas.append(
                    TeacherAssignment(
                        school_id=school.id,
                        class_subject_id=cs.id,
                        teacher_id=t_id,
                        assigned_by_id=admin.id,
                        is_active=True,
                    )
                )
                ta_keys.add((cs.id, t_id))

            if cs.id not in defs_by_cs_id:
                cs_defs: list[AssessmentDefinition] = []
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
                    new_defs.append(d)
                    cs_defs.append(d)
                defs_by_cs_id[cs.id] = cs_defs
        if new_tas:
            db.add_all(new_tas)
        if new_defs:
            db.add_all(new_defs)
        if new_tas or new_defs:
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

        print("Seeding students, enrollments, registrations, and scores...", flush=True)
        adm_counter = 1
        total_students = 0
        now_utc = datetime.now(UTC)

        existing_students = (
            (await db.execute(select(Student).where(Student.school_id == school.id)))
            .scalars()
            .all()
        )
        students_by_adm = {s.admission_number: s for s in existing_students}

        planned_students: list[tuple[Student, ClassGroup, str, list[str], float]] = []
        new_students: list[Student] = []

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

                student = students_by_adm.get(adm_num)
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
                    new_students.append(student)
                    students_by_adm[adm_num] = student

                chosen_codes = list(dept_core_codes[d_code])
                chosen_codes.extend(rng.choice(dept_elective_pools[d_code]))
                chosen_codes.append(rng.choice(trade_pool_by_dept[d_code]))
                student_ability = rng.uniform(0.45, 0.92)
                planned_students.append((student, cg, d_code, chosen_codes, student_ability))

        if new_students:
            db.add_all(new_students)
            await db.flush()

        existing_enrollments = (
            (
                await db.execute(
                    select(StudentEnrollment).where(
                        StudentEnrollment.school_id == school.id,
                        StudentEnrollment.academic_session_id == session.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        enrollments_by_student_id = {e.student_id: e for e in existing_enrollments}
        new_enrollments: list[StudentEnrollment] = []
        enrolled_plans: list[tuple[StudentEnrollment, ClassGroup, list[str], float]] = []

        for student, cg, _d_code, chosen_codes, student_ability in planned_students:
            enrollment = enrollments_by_student_id.get(student.id)
            if enrollment is None:
                enrollment = StudentEnrollment(
                    school_id=school.id,
                    student_id=student.id,
                    academic_session_id=session.id,
                    class_group_id=cg.id,
                    enrollment_status="ENROLLED",
                )
                new_enrollments.append(enrollment)
                enrollments_by_student_id[student.id] = enrollment
            enrolled_plans.append((enrollment, cg, chosen_codes, student_ability))

        if new_enrollments:
            db.add_all(new_enrollments)
            await db.flush()

        existing_regs = (
            await db.execute(
                select(
                    StudentSubjectRegistration.student_enrollment_id,
                    StudentSubjectRegistration.class_subject_id,
                ).where(StudentSubjectRegistration.school_id == school.id)
            )
        ).all()
        reg_keys = {(eid, csid) for eid, csid in existing_regs}

        existing_scores = (
            await db.execute(
                select(
                    AssessmentScore.assessment_definition_id,
                    AssessmentScore.student_enrollment_id,
                ).where(AssessmentScore.school_id == school.id)
            )
        ).all()
        score_keys = {(did, eid) for did, eid in existing_scores}

        new_regs: list[StudentSubjectRegistration] = []
        new_scores: list[AssessmentScore] = []

        for enrollment, cg, chosen_codes, student_ability in enrolled_plans:
            for code in chosen_codes:
                cs = cs_by_cg_and_code.get((cg.id, code))
                if cs is None:
                    continue
                if (enrollment.id, cs.id) not in reg_keys:
                    new_regs.append(
                        StudentSubjectRegistration(
                            school_id=school.id,
                            student_enrollment_id=enrollment.id,
                            class_subject_id=cs.id,
                            is_active=True,
                        )
                    )
                    reg_keys.add((enrollment.id, cs.id))

                t_id = subject_teacher_by_code[code]
                for defn in defs_by_cs_id.get(cs.id, []):
                    if (defn.id, enrollment.id) in score_keys:
                        continue
                    max_val = float(defn.max_score)
                    raw_val = round(
                        max(
                            2.0,
                            min(max_val, max_val * (student_ability + rng.uniform(-0.12, 0.10))),
                        ),
                        1,
                    )
                    new_scores.append(
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
                    score_keys.add((defn.id, enrollment.id))

        if new_regs:
            db.add_all(new_regs)
        if new_scores:
            db.add_all(new_scores)

        # Mark all ClassSubject score sheets as APPROVED so Results & Broadsheets can be tested right away
        existing_statuses = (
            (
                await db.execute(
                    select(ScoreSheetStatus.class_subject_id).where(
                        ScoreSheetStatus.school_id == school.id,
                        ScoreSheetStatus.academic_term_id == term.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        status_cs_ids = set(existing_statuses)
        new_statuses: list[ScoreSheetStatus] = []
        for (_, _), cs in cs_by_cg_and_code.items():
            if cs.id not in status_cs_ids:
                new_statuses.append(
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
                status_cs_ids.add(cs.id)
        if new_statuses:
            db.add_all(new_statuses)

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
