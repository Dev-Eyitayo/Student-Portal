"""Full realistic reseed for Olivet Baptist High School (OBHS-OYO): Junior + Senior
Secondary, NERDC curriculum, trade subjects, joint-class electives, and a
generated timetable -- meant to replace the ad-hoc state left behind by manual
UI testing.

Preserves: the School row (except `school_sector`, forced to PUBLIC -- see
below) and every existing User/StaffProfile/SchoolRolePermission/
UserPermissionGrant record (so the principal's and existing staff's logins
keep working).

Rebuilds from scratch: academic session/term, levels (JSS1-3, SS1-3), arms,
departments, class groups, subjects/offerings/class subjects, students,
enrollments, subject registrations, assessment definitions, assessment
scores, and every timetable constraint tab (bell schedules, rooms,
scheduling rules, sync groups). Does NOT create a Timetable draft or run
generation -- that's left for you to trigger from the UI.

Interpretation notes (since a few instructions were judgment calls):
  - "Core subjects" getting English/Maths' 3-periods-with-one-double
    treatment means the NERDC *compulsory, academically weighty* subjects per
    level (JSS: ENG, MTH, Basic Science, Basic Technology, Civic Education;
    SS: ENG, MTH, Civic + each department's compulsory non-elective subjects
    e.g. Physics/Chemistry/Biology for Science). Lighter compulsory subjects
    (PHE, CCA, Home Economics, Security Education, Business Studies, Social
    Studies) and all true electives/trade subjects get 1-2 periods/week with
    no double, matching "the rest can have two or one periods per week."
  - This school is forced to `school_sector=PUBLIC` on every run so its
    SchoolConfiguration.assessment_weight_schema matches the TESCOM Mark
    Book template (ATTENDANCE/NOTEBOOK/OPEN_DAY/TEXTBOOK/SB_TEST/TB_ASSESS,
    each 5%, + EXAM at 70%) instead of the generic private-school
    TEST1/TEST2/ASSIGN/EXAM default -- see app.modules.assessments.mark_book
    and `GET /api/v1/assessments/mark-book/export`.
  - Every class arm gets a class (form) teacher, and assessment scores are
    seeded at ~96% completion (APPROVED score sheets) for First Term, so the
    Mark Book export, broadsheets, and report cards all have realistic,
    near-finished data to check against -- rather than an empty gradebook.

Usage:
    .venv/bin/python -m scripts.seed_realistic_school
"""

import asyncio
import logging
import math
import random
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select, text
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
from app.modules.assessments.services import seed_assessment_definitions_for_term
from app.modules.audit.models import AuditLog
from app.modules.audit.services import AuditContext
from app.modules.computation.models import SubjectResult, TermReportCard
from app.modules.distribution.models import ResultAccessToken
from app.modules.identity.models import School, SchoolConfiguration, StaffProfile, User
from app.modules.identity.schemas import SchoolSector
from app.modules.identity.services import PUBLIC_TESCOM_ASSESSMENT_WEIGHTS
from app.modules.students.models import (
    AttendanceRecord,
    AttendanceSummary,
    Student,
    StudentEnrollment,
)
from app.modules.students.services import auto_register_compulsory_subjects
from app.modules.subjects.models import (
    ClassSubject,
    StudentSubjectRegistration,
    Subject,
    SubjectOffering,
    TeacherAssignment,
)
from app.modules.timetable.models import (
    BellSchedule,
    Room,
    SubjectSchedulingRule,
    TeacherAvailability,
    Timetable,
    TimetableEntry,
    TimetableEntryTeacher,
    TimetableSlot,
    TimetableSyncGroup,
    TimetableSyncGroupItem,
)

DEMO_PASSWORD = "OlivetDemo123!"
SESSION_NAME = "2026/2027"

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

# ---------------------------------------------------------------------------
# NERDC-style subject catalogs (kept consistent with
# app.modules.academic_structure.templates so the rest of the system's
# subject taxonomy doesn't diverge from this reseed).
# ---------------------------------------------------------------------------
# (code, name, is_elective, is_trade_subject, department_codes|None)
JSS_SUBJECTS = [
    ("ENG", "English Language", False, False, None),
    ("MTH", "Mathematics", False, False, None),
    ("BSC", "Basic Science", False, False, None),
    ("BTC", "Basic Technology", False, False, None),
    ("BUS", "Business Studies", False, False, None),
    ("SOS", "Social Studies", False, False, None),
    ("CIV", "Civic Education", False, False, None),
    ("SEC", "Security Education", False, False, None),
    ("AGR", "Agricultural Science", False, False, None),
    ("HEC", "Home Economics", False, False, None),
    ("PHE", "Physical & Health Education", False, False, None),
    ("CCA", "Cultural & Creative Arts", False, False, None),
    ("CMP", "Computer Studies", False, False, None),
    ("CRS", "Christian Religious Studies", True, False, None),
    ("IRS", "Islamic Religious Studies", True, False, None),
    ("YOR", "Yoruba Language", True, False, None),
    ("FRN", "French", True, False, None),
]
JSS_CORE_CODES = {"ENG", "MTH", "BSC", "BTC", "CIV"}

SS_SUBJECTS = [
    ("ENG", "English Language", False, False, ["SCI", "ART", "COMM"]),
    ("MTH", "Mathematics", False, False, ["SCI", "ART", "COMM"]),
    ("CIV", "Civic Education", False, False, ["SCI", "ART", "COMM"]),
    ("PHY", "Physics", False, False, ["SCI"]),
    ("CHM", "Chemistry", False, False, ["SCI"]),
    ("BIO", "Biology", False, False, ["SCI"]),
    ("FMA", "Further Mathematics", True, False, ["SCI"]),
    ("AGR", "Agricultural Science", True, False, ["SCI"]),
    ("GEO", "Geography", True, False, ["SCI", "ART"]),
    ("TDW", "Technical Drawing", True, False, ["SCI"]),
    ("LIT", "Literature-in-English", False, False, ["ART"]),
    ("GOV", "Government", False, False, ["ART", "COMM"]),
    ("HIS", "History", False, False, ["ART"]),
    ("CRS", "Christian Religious Studies", True, False, ["ART"]),
    ("IRS", "Islamic Religious Studies", True, False, ["ART"]),
    ("YOR", "Yoruba Language", True, False, ["ART"]),
    ("ACC", "Financial Accounting", False, False, ["COMM"]),
    ("COM", "Commerce", False, False, ["COMM"]),
    ("ECO", "Economics", False, False, ["COMM", "ART"]),
    ("BKM", "Book Keeping", True, False, ["COMM"]),
    ("OFP", "Office Practice", True, False, ["COMM"]),
    ("DPR", "Data Processing", True, True, ["SCI", "ART", "COMM"]),
    ("CAT", "Catering Craft Practice", True, True, ["SCI", "ART", "COMM"]),
    ("GAR", "Garment Making", True, True, ["ART", "COMM"]),
    ("FIS", "Fisheries", True, True, ["SCI", "COMM"]),
    ("MKT", "Marketing", True, True, ["SCI", "ART", "COMM"]),
]
SS_DEPT_CORE_BY_DEPT = {
    "SCI": {"PHY", "CHM", "BIO"},
    "ART": {"LIT", "GOV", "HIS"},
    "COMM": {"ACC", "COM", "ECO"},
}

LAB_SUBJECT_MAP = {
    "PHY": ("Physics Lab", "PHY-LAB", "SCIENCE_LAB", 50),
    "CHM": ("Chemistry Lab", "CHM-LAB", "SCIENCE_LAB", 50),
    "BIO": ("Biology Lab", "BIO-LAB", "SCIENCE_LAB", 50),
    "CMP": ("ICT Room", "ICT-ROOM", "ICT_LAB", 45),
    "CAT": ("Catering Kitchen", "CAT-KITCHEN", "WORKSHOP", 20),
    "GAR": ("Garment Workshop", "GAR-WORKSHOP", "WORKSHOP", 20),
    "TDW": ("Technical Drawing Studio", "TDW-STUDIO", "WORKSHOP", 30),
}

# The original staff roster created by scripts.seed_demo (principal +
# academic officer + the 14 subject teachers). Anyone else in the `users`
# table for this school was created by a PRIOR run of this script -- since
# this script preserves logins rather than wiping Users outright, those
# disposable new-hire accounts must be explicitly cleaned up on every reseed,
# or they'd accumulate forever across repeated runs.
ORIGINAL_STAFF_EMAILS = {
    "folake.adebayo@olivet.edu.ng",
    "kunle.adeyemi@olivet.edu.ng",
    "tunde.balogun@olivet.edu.ng",
    "grace.ojo@olivet.edu.ng",
    "ibrahim.musa@olivet.edu.ng",
    "chidinma.nwosu@olivet.edu.ng",
    "aisha.adamu@olivet.edu.ng",
    "emeka.eze@olivet.edu.ng",
    "olumide.adekunle@olivet.edu.ng",
    "bukola.olatunji@olivet.edu.ng",
    "sadiq.bello@olivet.edu.ng",
    "ngozi.okonkwo@olivet.edu.ng",
    "kehinde.alabi@olivet.edu.ng",
    "funke.ajayi@olivet.edu.ng",
    "usman.garba@olivet.edu.ng",
    "segun.bankole@olivet.edu.ng",
}

# Existing staff (preserved logins) mapped to the subject area they already
# cover, reused as the *first* teacher in that subject's pool.
EXISTING_STAFF_SUBJECT = {
    "tunde.balogun@olivet.edu.ng": "MTH",
    "grace.ojo@olivet.edu.ng": "CHM",
    "ibrahim.musa@olivet.edu.ng": "PHY",
    "chidinma.nwosu@olivet.edu.ng": "ENG",
    "aisha.adamu@olivet.edu.ng": "BIO",
    "emeka.eze@olivet.edu.ng": "CIV",
    "olumide.adekunle@olivet.edu.ng": "LIT",
    "bukola.olatunji@olivet.edu.ng": "ACC",
    "sadiq.bello@olivet.edu.ng": "ECO",
    "ngozi.okonkwo@olivet.edu.ng": "DPR",
    "kehinde.alabi@olivet.edu.ng": "AGR",
    "funke.ajayi@olivet.edu.ng": "CAT",
    "usman.garba@olivet.edu.ng": "CRS",
    "segun.bankole@olivet.edu.ng": "TDW",
}

# New hires needed to cover JSS-only subjects no existing staff specializes
# in, and a couple more SS specialists so pools don't collapse onto too few
# people.
NEW_TEACHER_SUBJECT_SEEDS = [
    ("Adebisi", "Ogunleye", "BTC"),
    ("Chinedu", "Okafor", "BUS"),
    ("Blessing", "Etim", "SOS"),
    ("Yusuf", "Abdullahi", "SEC"),
    ("Nneka", "Uche", "HEC"),
    ("Babatunde", "Fashola", "PHE"),
    ("Zainab", "Suleiman", "CCA"),
    ("Oluwaseun", "Akintola", "CMP"),
    ("Hauwa", "Danladi", "GOV"),
    ("Rotimi", "Williams", "HIS"),
    ("Chisom", "Okeke", "COM"),
    ("Sola", "Ogundipe", "GEO"),
    ("Aminu", "Bello", "FMA"),
    ("Titilayo", "Aina", "GAR"),
    ("Femi", "Osho", "MKT"),
    ("Nadia", "Mohammed", "FIS"),
    ("Gideon", "Adebisi", "OFP"),
    ("Rukayat", "Lawal", "BKM"),
    ("Damilola", "Oladipo", "YOR"),
    ("Ifeoma", "Nwachukwu", "IRS"),
    ("Kemi", "Adedoyin", "FRN"),
]

# Deliberate, light-touch JSS<->SS crossovers: each pair reassigns ONE
# existing Senior class subject (of the paired ss_code) to the teacher who
# already covers jss_code in Junior -- a small, explicit footprint (one extra
# arm each), not a wholesale pool merge. Where jss_code == ss_code (e.g.
# "AGR"/"AGR") it's literally the same subject taught across both; the rest
# are a plausible adjacent subject for that teacher's background.
CROSS_CATEGORY_PAIRS: list[tuple[str, str]] = [
    ("ENG", "ENG"), ("MTH", "MTH"), ("CIV", "CIV"),
    ("BSC", "BIO"), ("BTC", "PHY"), ("BUS", "ACC"), ("SOS", "HIS"),
    ("AGR", "AGR"), ("CRS", "CRS"), ("IRS", "IRS"), ("YOR", "YOR"),
    ("SEC", "GOV"), ("HEC", "CAT"), ("PHE", "MKT"), ("CMP", "DPR"),
]

# Weekly periods/teacher cap used to size each subject's teacher pool -- a
# believable full-time load for a Nigerian secondary school teacher.
TEACHER_LOAD_CAP = 24

CORE_PERIODS_PER_WEEK = 3
CORE_DOUBLE_PERIODS_PER_WEEK = 1
ELECTIVE_PERIODS_CHOICES = [1, 2]

SCIENCE_ARMS = ["O", "L", "I", "V"]
COMMERCIAL_ARM = ["E"]
ARTS_ARMS = ["T", "B", "H", "S", "Y"]
JSS_ARM_LETTERS = ["A", "B", "C"]

SS_LEVEL_SPECS = [
    ("SS 1", "SS1", 4, "SENIOR_SECONDARY"),
    ("SS 2", "SS2", 5, "SENIOR_SECONDARY"),
    ("SS 3", "SS3", 6, "SENIOR_SECONDARY"),
]
JSS_LEVEL_SPECS = [
    ("JSS 1", "JSS1", 1, "JUNIOR_SECONDARY"),
    ("JSS 2", "JSS2", 2, "JUNIOR_SECONDARY"),
    ("JSS 3", "JSS3", 3, "JUNIOR_SECONDARY"),
]

JSS_SLOTS = [
    ("Morning Assembly", time(7, 45), time(8, 0), "ASSEMBLY"),
    ("Period 1", time(8, 0), time(8, 35), "TEACHING"),
    ("Period 2", time(8, 35), time(9, 10), "TEACHING"),
    ("Short Break", time(9, 10), time(9, 20), "SHORT_BREAK"),
    ("Period 3", time(9, 20), time(9, 55), "TEACHING"),
    ("Period 4", time(9, 55), time(10, 30), "TEACHING"),
    ("Long Break", time(10, 30), time(11, 0), "LONG_BREAK"),
    ("Period 5", time(11, 0), time(11, 35), "TEACHING"),
    ("Period 6", time(11, 35), time(12, 10), "TEACHING"),
    ("Period 7", time(12, 10), time(12, 45), "TEACHING"),
]
JSS_THURSDAY_SLOTS = [
    ("Morning Assembly", time(7, 45), time(8, 0), "ASSEMBLY"),
    ("Environmental Sanitation", time(8, 0), time(8, 35), "CLUBS_SPORTS"),
    ("Environmental Sanitation", time(8, 35), time(9, 10), "CLUBS_SPORTS"),
    ("Period 1", time(9, 10), time(9, 45), "TEACHING"),
    ("Short Break", time(9, 45), time(9, 55), "SHORT_BREAK"),
    ("Period 2", time(9, 55), time(10, 30), "TEACHING"),
    ("Long Break", time(10, 30), time(11, 0), "LONG_BREAK"),
    ("Period 3", time(11, 0), time(11, 35), "TEACHING"),
    ("Period 4", time(11, 35), time(12, 10), "TEACHING"),
    ("Period 5", time(12, 10), time(12, 45), "TEACHING"),
]

SS_SLOTS = [
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
SS_THURSDAY_SLOTS = [
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


def _build_week_slots(
    school_id: UUID,
    bell_id: UUID,
    *,
    weekday_slots,
    thursday_slots,
    friday_cutoff: time,
    tuesday_club_from: str | None,
) -> list[TimetableSlot]:
    slots: list[TimetableSlot] = []
    for day in range(1, 6):
        if day == 4:
            rows = thursday_slots
        else:
            rows = weekday_slots
        for order, (label, start_t, end_t, slot_type) in enumerate(rows, start=1):
            if day == 5 and end_t > friday_cutoff:
                continue
            row_label, row_type = label, slot_type
            if day == 2 and tuesday_club_from is not None and label == tuesday_club_from:
                row_label, row_type = "Literary & Debate", "CLUBS_SPORTS"
            if day == 2 and tuesday_club_from is not None:
                # Everything from tuesday_club_from onward becomes club time.
                names = [r[0] for r in rows]
                if names.index(label) >= names.index(tuesday_club_from):
                    row_label, row_type = "Literary & Debate", "CLUBS_SPORTS"
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


async def _reset_school_data(db: AsyncSession, school: School) -> None:
    """Deletes everything for this school EXCEPT the School row,
    SchoolConfiguration, and User/StaffProfile/permission records -- so
    existing logins keep working."""
    sid = school.id
    for model in [
        # Timetable module (child-most first)
        TimetableEntryTeacher,
        TimetableEntry,
        Timetable,
        TimetableSyncGroupItem,
        TimetableSyncGroup,
        TeacherAvailability,
        SubjectSchedulingRule,
        TimetableSlot,
        BellSchedule,
        Room,
        # Results / assessments
        ResultAccessToken,
        SubjectResult,
        TermReportCard,
        AssessmentScore,
        ScoreSheetStatus,
        AssessmentDefinition,
        AttendanceRecord,
        AttendanceSummary,
        # Enrollment / registration graph
        StudentSubjectRegistration,
        TeacherAssignment,
        ClassSubject,
        SubjectOffering,
        StudentEnrollment,
        Student,
        ClassGroup,
        # Academic structure
        AcademicTerm,
        AcademicSession,
        ClassArm,
        Department,
        AcademicLevel,
        Subject,
        # Audit trail for this school (references resources we just dropped)
        AuditLog,
    ]:
        await db.execute(delete(model).where(model.school_id == sid))

    # Any User not in ORIGINAL_STAFF_EMAILS was a disposable new-hire teacher
    # created by a PRIOR run of THIS script, not a real staff login to
    # preserve -- without this, every reseed would pile its own random new
    # hires on top of the last run's, growing the roster forever.
    disposable_user_ids = (
        (
            await db.execute(
                select(User.id).where(
                    User.school_id == sid, User.email.not_in(ORIGINAL_STAFF_EMAILS)
                )
            )
        )
        .scalars()
        .all()
    )
    if disposable_user_ids:
        await db.execute(delete(StaffProfile).where(StaffProfile.user_id.in_(disposable_user_ids)))
        await db.execute(delete(User).where(User.id.in_(disposable_user_ids)))

    await db.commit()
    print(
        f"Flushed academic structure, students, subjects, assessments, and timetable data; "
        f"removed {len(disposable_user_ids)} disposable teacher accounts from prior runs "
        f"(kept the {len(ORIGINAL_STAFF_EMAILS)} original staff logins)."
    )


async def _load_existing_staff(db: AsyncSession, school_id: UUID) -> dict[str, User]:
    rows = (
        (
            await db.execute(
                select(User).where(User.school_id == school_id, User.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    return {u.email: u for u in rows}


async def _ensure_teacher(
    db: AsyncSession,
    school_id: UUID,
    *,
    email: str,
    first_name: str,
    last_name: str,
    by_email: dict[str, User],
    rng: random.Random,
) -> User:
    existing = by_email.get(email)
    if existing is not None:
        return existing
    user = User(
        school_id=school_id,
        email=email,
        password_hash=hash_password(DEMO_PASSWORD),
        first_name=first_name,
        last_name=last_name,
        phone_number=f"+23480{rng.randint(10000000, 99999999)}",
        role="TEACHER",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    existing_profiles = (
        await db.execute(
            select(func.count())
            .select_from(StaffProfile)
            .where(StaffProfile.school_id == school_id)
        )
    ).scalar_one()
    db.add(
        StaffProfile(
            school_id=school_id,
            user_id=user.id,
            staff_id_number=f"OBHS/STF/NEW/{existing_profiles + 1:03d}",
            employment_status="ACTIVE",
            specialization=last_name,
        )
    )
    by_email[email] = user
    return user


def _random_dob(rng: random.Random, level_code: str) -> date:
    # Roughly realistic ages per Nigerian secondary level.
    birth_year = {
        "JSS1": 2014,
        "JSS2": 2013,
        "JSS3": 2012,
        "SS1": 2011,
        "SS2": 2010,
        "SS3": 2009,
    }[level_code]
    return date(birth_year, rng.randint(1, 12), rng.randint(1, 28))


async def seed() -> None:
    logging.disable(logging.INFO)
    rng = random.Random(20261124)
    # A separate stream purely for picking cosmetic names (new teachers,
    # students). Keeping this off the main `rng` means how many names get
    # drawn (which depends on the teacher load cap, elective skip rolls,
    # etc.) never shifts the sequence of *structural* random decisions
    # (elective period counts, arm-roster cutoffs...) -- otherwise tuning
    # TEACHER_LOAD_CAP would unpredictably perturb unrelated outcomes.
    naming_rng = random.Random(20261125)

    async with async_session_factory() as db:
        await db.execute(text("SELECT set_config('app.is_super_admin', 'true', true)"))
        school = (
            await db.execute(select(School).where(School.official_code == "OBHS-OYO"))
        ).scalar_one_or_none()
        if school is None:
            raise RuntimeError(
                "School 'OBHS-OYO' not found. Run scripts.seed_demo first to create the "
                "school and its staff accounts (this script only reseeds structure/"
                "students/timetable on top of an existing school)."
            )
        _tenant_school_id.set(str(school.id))
        await db.execute(
            text("SELECT set_config('app.current_school_id', :sid, false)"), {"sid": str(school.id)}
        )

        # Ensure this school is configured as a PUBLIC-sector secondary school so
        # its assessment components match the TESCOM Mark Book structure
        # (ATTENDANCE/NOTEBOOK/OPEN_DAY/TEXTBOOK/SB_TEST/TB_ASSESS + EXAM) instead
        # of the generic private-school TEST1/TEST2/ASSIGN/EXAM default -- this
        # runs on every reseed so it self-heals regardless of how the school row
        # was originally created.
        config = (
            await db.execute(
                select(SchoolConfiguration).where(SchoolConfiguration.school_id == school.id)
            )
        ).scalar_one_or_none()
        if config is None:
            raise RuntimeError(
                "No SchoolConfiguration found for 'OBHS-OYO'. Run scripts.seed_demo first."
            )
        school.school_sector = SchoolSector.PUBLIC.value
        config.assessment_weight_schema = deepcopy(PUBLIC_TESCOM_ASSESSMENT_WEIGHTS)
        await db.flush()
        print("Set school sector to PUBLIC and assessment weight schema to the TESCOM structure.")

        await _reset_school_data(db, school)

        staff_by_email = await _load_existing_staff(db, school.id)
        admin = next((u for u in staff_by_email.values() if u.role == "SCHOOL_ADMIN"), None)
        if admin is None:
            raise RuntimeError(
                "No SCHOOL_ADMIN user found for this school to use as the audit actor."
            )
        audit = AuditContext(school_id=school.id, actor_id=admin.id, actor_ip="127.0.0.1")

        # ------------------------------------------------------------------
        # 1. Teacher pools per subject -- existing staff reused as pool[0]
        #    where their specialization matches; new hires fill the rest.
        # ------------------------------------------------------------------
        print("Ensuring teacher roster...", flush=True)
        subject_teacher_seed: dict[str, User] = {}
        for email, code in EXISTING_STAFF_SUBJECT.items():
            if email in staff_by_email:
                subject_teacher_seed[code] = staff_by_email[email]
        for fn, ln, code in NEW_TEACHER_SUBJECT_SEEDS:
            email = f"{fn.lower()}.{ln.lower()}@olivet.edu.ng"
            subject_teacher_seed[code] = await _ensure_teacher(
                db,
                school.id,
                email=email,
                first_name=fn,
                last_name=ln,
                by_email=staff_by_email,
                rng=naming_rng,
            )
        await db.flush()

        # ------------------------------------------------------------------
        # 2. Session, Term, Levels
        # ------------------------------------------------------------------
        today = date.today()
        session = AcademicSession(
            school_id=school.id,
            name=SESSION_NAME,
            start_date=today - timedelta(days=45),
            end_date=today + timedelta(days=270),
            is_current=True,
        )
        db.add(session)
        await db.flush()

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

        print("Seeding levels, departments, arms, and class groups...", flush=True)
        levels_by_code: dict[str, AcademicLevel] = {}
        for name, code, order, cat in JSS_LEVEL_SPECS + SS_LEVEL_SPECS:
            lvl = AcademicLevel(
                school_id=school.id, name=name, code=code, level_order=order, category=cat
            )
            db.add(lvl)
            levels_by_code[code] = lvl
        await db.flush()

        depts_by_code: dict[str, Department] = {}
        for name, code in [("Science", "SCI"), ("Arts", "ART"), ("Commercial", "COMM")]:
            dept = Department(school_id=school.id, name=name, code=code)
            db.add(dept)
            depts_by_code[code] = dept
        await db.flush()

        # "B" appears in both JSS_ARM_LETTERS and ARTS_ARMS -- dedupe so the
        # same ClassArm row ("B") is simply reused across levels rather than
        # inserted twice (class_arms.name is unique per school).
        arm_letters = list(dict.fromkeys(JSS_ARM_LETTERS + SCIENCE_ARMS + COMMERCIAL_ARM + ARTS_ARMS))
        arms_by_letter: dict[str, ClassArm] = {}
        for letter in arm_letters:
            arm = ClassArm(school_id=school.id, name=letter)
            db.add(arm)
            arms_by_letter[letter] = arm
        await db.flush()

        # JSS: simple, non-departmentalized arms (A, B, C).
        class_groups: list[ClassGroup] = []
        cg_dept_code: dict[UUID, str | None] = {}
        for lvl_code in ("JSS1", "JSS2", "JSS3"):
            lvl = levels_by_code[lvl_code]
            for letter in JSS_ARM_LETTERS:
                arm = arms_by_letter[letter]
                cg = ClassGroup(
                    school_id=school.id,
                    academic_session_id=session.id,
                    academic_level_id=lvl.id,
                    department_id=None,
                    class_arm_id=arm.id,
                    display_name=f"{lvl.name} {arm.name}",
                )
                db.add(cg)
                class_groups.append(cg)
                cg_dept_code[id(cg)] = None

        # SS: OLIVETBHSY. The arm roster fluctuates per level between "up to
        # H" and "up to S" (rarely the full "up to Y"), and the Commercial
        # (E) arm is real but always the thinnest in enrollment.
        arts_cutoff_choices = [3, 3, 4, 4, 5]  # mostly H (3 arts arms) or S (4), rarely Y (5)
        ss_level_arm_plan: dict[str, list[tuple[str, str]]] = {}
        for lvl_code in ("SS1", "SS2", "SS3"):
            n_arts = rng.choice(arts_cutoff_choices)
            plan: list[tuple[str, str]] = [(letter, "SCI") for letter in SCIENCE_ARMS]
            plan.append(("E", "COMM"))
            plan.extend((letter, "ART") for letter in ARTS_ARMS[:n_arts])
            ss_level_arm_plan[lvl_code] = plan

        for lvl_code, plan in ss_level_arm_plan.items():
            lvl = levels_by_code[lvl_code]
            for letter, dept_code in plan:
                arm = arms_by_letter[letter]
                dept = depts_by_code[dept_code]
                cg = ClassGroup(
                    school_id=school.id,
                    academic_session_id=session.id,
                    academic_level_id=lvl.id,
                    department_id=dept.id,
                    class_arm_id=arm.id,
                    display_name=f"{lvl.name} {dept.name} {arm.name}",
                )
                db.add(cg)
                class_groups.append(cg)
                cg_dept_code[id(cg)] = dept_code
        await db.flush()
        print(
            f"Created {len(levels_by_code)} levels, {len(class_groups)} class groups "
            f"({len(JSS_ARM_LETTERS) * 3} JSS arms + {len(class_groups) - len(JSS_ARM_LETTERS) * 3} SS arms)."
        )

        # ------------------------------------------------------------------
        # 3. Subjects, offerings, class subjects, teacher assignments
        # ------------------------------------------------------------------
        print("Seeding subjects, offerings, class subjects, and teacher assignments...", flush=True)
        subjects_by_code: dict[str, Subject] = {}
        for code, name, is_elec, is_trade, _depts in JSS_SUBJECTS + SS_SUBJECTS:
            if code in subjects_by_code:
                continue
            # AGR is compulsory in JSS (handled explicitly in step 5) but elective in SS.
            effective_elec = True if code == "AGR" else is_elec
            subj = Subject(
                school_id=school.id,
                code=code,
                name=name,
                is_elective=effective_elec,
                is_trade_subject=is_trade,
            )
            db.add(subj)
            subjects_by_code[code] = subj
        await db.flush()

        rooms_by_code: dict[str, Room] = {}
        for _subj_code, (r_name, r_code, r_type, cap) in LAB_SUBJECT_MAP.items():
            room = Room(
                school_id=school.id, name=r_name, code=r_code, room_type=r_type, capacity=cap
            )
            db.add(room)
            rooms_by_code[r_code] = room
        await db.flush()

        # Per-subject teacher pools, sized to keep individual load believable
        # (TEACHER_LOAD_CAP periods/week), seeded with the matching
        # existing/new hire as pool[0]. `pool_key` namespaces JSS and SS pools
        # separately (e.g. "JSS:AGR" vs "SS:AGR") so a subject code that
        # exists in both catalogs never silently shares one pool between
        # categories -- the only JSS<->SS overlap is the deliberate, limited
        # CROSS_CATEGORY_PAIRS reassignment pass below.
        subject_pools: dict[str, list[User]] = {}

        async def build_pool(
            pool_key: str, seed_code: str | None, demand_arms: int, periods: int
        ) -> list[User]:
            pool = subject_pools.get(pool_key)
            if pool is not None:
                return pool
            n_needed = max(1, math.ceil(demand_arms * periods / TEACHER_LOAD_CAP))
            pool = (
                [subject_teacher_seed[seed_code]]
                if seed_code is not None and seed_code in subject_teacher_seed
                else []
            )
            idx = 1
            tag = pool_key.lower().replace(":", "")
            while len(pool) < n_needed:
                fn = naming_rng.choice(MALE_FIRST_NAMES + FEMALE_FIRST_NAMES)
                ln = naming_rng.choice(LAST_NAMES)
                email = f"{fn.lower()}.{ln.lower()}.{tag}{idx}@olivet.edu.ng"
                t = await _ensure_teacher(
                    db,
                    school.id,
                    email=email,
                    first_name=fn,
                    last_name=ln,
                    by_email=staff_by_email,
                    rng=naming_rng,
                )
                pool.append(t)
                idx += 1
            subject_pools[pool_key] = pool
            return pool

        jss_subject_codes = {c[0] for c in JSS_SUBJECTS}

        cs_by_cg_and_code: dict[tuple[UUID, str], ClassSubject] = {}

        # SubjectOffering.academic_level_id is NOT NULL -- offerings must be
        # created per level (not shared across all 3 JSS/SS levels), so group
        # class groups by level (and by level+department for SS).
        level_id_to_code = {lvl.id: code for code, lvl in levels_by_code.items()}
        jss_groups_by_level: dict[str, list[ClassGroup]] = {"JSS1": [], "JSS2": [], "JSS3": []}
        ss_groups_by_level_dept: dict[tuple[str, str], list[ClassGroup]] = {}
        for cg in class_groups:
            lvl_code = level_id_to_code[cg.academic_level_id]
            dcode = cg_dept_code[id(cg)]
            if dcode is None:
                jss_groups_by_level[lvl_code].append(cg)
            else:
                ss_groups_by_level_dept.setdefault((lvl_code, dcode), []).append(cg)

        # -- JSS offerings/class subjects (one offering per level) --
        # JSS has 33 teaching slots/week: 5 core * 3 = 15, leaving 18 slots for 12 subjects.
        # Cap at 5 two-period non-core subjects + 7 one-period subjects = 32 <= 33 slots.
        jss_non_core_codes = [c[0] for c in JSS_SUBJECTS if c[0] not in JSS_CORE_CODES]
        jss_two_period_codes = set(
            rng.sample(jss_non_core_codes, k=min(5, len(jss_non_core_codes)))
        )
        cs_teacher_assignments: dict[UUID, TeacherAssignment] = {}
        for code, _name, _is_elec, _is_trade, _depts in JSS_SUBJECTS:
            if code in JSS_CORE_CODES:
                periods = CORE_PERIODS_PER_WEEK
            else:
                periods = 2 if code in jss_two_period_codes else 1
            doubles = CORE_DOUBLE_PERIODS_PER_WEEK if code in JSS_CORE_CODES else 0
            total_jss_arms = sum(len(g) for g in jss_groups_by_level.values())
            pool = await build_pool(f"JSS:{code}", code, total_jss_arms, periods)

            db.add(
                SubjectSchedulingRule(
                    school_id=school.id,
                    academic_session_id=session.id,
                    academic_level_id=None,
                    department_id=None,
                    subject_id=subjects_by_code[code].id,
                    periods_per_week=periods,
                    double_periods_per_week=doubles,
                    allow_double_period_split=False,
                    max_periods_per_day=2 if doubles else 1,
                    prefer_morning=code in JSS_CORE_CODES,
                    preferred_room_id=rooms_by_code[LAB_SUBJECT_MAP[code][1]].id
                    if code in LAB_SUBJECT_MAP
                    else None,
                    room_only_for_double_periods=True,
                )
            )

            global_i = 0
            for lvl_code in ("JSS1", "JSS2", "JSS3"):
                groups = jss_groups_by_level[lvl_code]
                if not groups:
                    continue
                offering = SubjectOffering(
                    school_id=school.id,
                    subject_id=subjects_by_code[code].id,
                    academic_session_id=session.id,
                    academic_level_id=levels_by_code[lvl_code].id,
                    department_id=None,
                )
                db.add(offering)
                await db.flush()
                for cg in groups:
                    cs = ClassSubject(
                        school_id=school.id, subject_offering_id=offering.id, class_group_id=cg.id
                    )
                    db.add(cs)
                    await db.flush()
                    cs_by_cg_and_code[(cg.id, code)] = cs
                    ta = TeacherAssignment(
                        school_id=school.id,
                        class_subject_id=cs.id,
                        teacher_id=pool[global_i % len(pool)].id,
                        assigned_by_id=admin.id,
                        is_active=True,
                    )
                    db.add(ta)
                    cs_teacher_assignments[cs.id] = ta
                    global_i += 1

        # -- SS offerings/class subjects (General when all 3 depts, else per level+department) --
        for code, _name, is_elec, _is_trade, depts in SS_SUBJECTS:
            # Choose elective period count once per subject so cross-dept JOINT_CLASS (e.g. MKT) matches.
            elective_periods = (
                rng.choice(ELECTIVE_PERIODS_CHOICES) if is_elec else CORE_PERIODS_PER_WEEK
            )
            is_general_ss = depts == ["SCI", "ART", "COMM"]
            general_offerings_by_level: dict[str, SubjectOffering] = {}
            if is_general_ss:
                for lvl_code in ("SS1", "SS2", "SS3"):
                    offering = SubjectOffering(
                        school_id=school.id,
                        subject_id=subjects_by_code[code].id,
                        academic_session_id=session.id,
                        academic_level_id=levels_by_code[lvl_code].id,
                        department_id=None,
                    )
                    db.add(offering)
                    await db.flush()
                    general_offerings_by_level[lvl_code] = offering

            # One teacher pool per subject across ALL departments it's offered
            # in (a Government teacher realistically covers both Arts and
            # Commercial sets) -- sized by total demand across every
            # department/level, not a fragmented per-department pool.
            total_subject_arms = sum(
                len(ss_groups_by_level_dept.get((lvl_code, d), []))
                for d in depts
                for lvl_code in ("SS1", "SS2", "SS3")
            )
            if not total_subject_arms:
                continue
            is_core_any_dept = code in ("ENG", "MTH", "CIV") or any(
                code in SS_DEPT_CORE_BY_DEPT.get(d, set()) for d in depts
            )
            subject_periods = CORE_PERIODS_PER_WEEK if is_core_any_dept else elective_periods
            # A code shared with the JSS catalog (AGR, CRS, IRS, YOR, ...)
            # must NOT silently reuse the JSS teacher as the SS pool's seed --
            # the only sanctioned overlap is the explicit CROSS_CATEGORY_PAIRS
            # pass below.
            seed_code = code if code not in jss_subject_codes else None
            pool = await build_pool(f"SS:{code}", seed_code, total_subject_arms, subject_periods)
            global_i = 0

            for dept_code in depts:
                groups_by_level = {
                    lvl_code: ss_groups_by_level_dept.get((lvl_code, dept_code), [])
                    for lvl_code in ("SS1", "SS2", "SS3")
                }
                total_dept_arms = sum(len(g) for g in groups_by_level.values())
                if not total_dept_arms:
                    continue

                is_core = code in ("ENG", "MTH", "CIV") or code in SS_DEPT_CORE_BY_DEPT.get(
                    dept_code, set()
                )
                periods = CORE_PERIODS_PER_WEEK if is_core else elective_periods
                doubles = CORE_DOUBLE_PERIODS_PER_WEEK if is_core else 0

                db.add(
                    SubjectSchedulingRule(
                        school_id=school.id,
                        academic_session_id=session.id,
                        academic_level_id=None,
                        department_id=depts_by_code[dept_code].id,
                        subject_id=subjects_by_code[code].id,
                        periods_per_week=periods,
                        double_periods_per_week=doubles,
                        allow_double_period_split=False,
                        max_periods_per_day=2 if doubles else 1,
                        prefer_morning=is_core,
                        preferred_room_id=rooms_by_code[LAB_SUBJECT_MAP[code][1]].id
                        if code in LAB_SUBJECT_MAP
                        else None,
                        room_only_for_double_periods=True,
                    )
                )

                for lvl_code in ("SS1", "SS2", "SS3"):
                    groups = groups_by_level[lvl_code]
                    if not groups:
                        continue
                    if is_general_ss:
                        offering = general_offerings_by_level[lvl_code]
                    else:
                        offering = SubjectOffering(
                            school_id=school.id,
                            subject_id=subjects_by_code[code].id,
                            academic_session_id=session.id,
                            academic_level_id=levels_by_code[lvl_code].id,
                            department_id=depts_by_code[dept_code].id,
                        )
                        db.add(offering)
                        await db.flush()
                    for cg in groups:
                        # Electives/trade subjects aren't taken by every arm.
                        if is_elec and rng.random() < 0.35:
                            continue
                        cs = ClassSubject(
                            school_id=school.id,
                            subject_offering_id=offering.id,
                            class_group_id=cg.id,
                        )
                        db.add(cs)
                        await db.flush()
                        cs_by_cg_and_code[(cg.id, code)] = cs
                        ta = TeacherAssignment(
                            school_id=school.id,
                            class_subject_id=cs.id,
                            teacher_id=pool[global_i % len(pool)].id,
                            assigned_by_id=admin.id,
                            is_active=True,
                        )
                        db.add(ta)
                        cs_teacher_assignments[cs.id] = ta
                        global_i += 1

        await db.flush()
        print(f"Created {len(cs_by_cg_and_code)} class-subject offerings across JSS and SS.")

        # ------------------------------------------------------------------
        # 3.5 Deliberate JSS<->SS crossovers: exactly CROSS_CATEGORY_PAIRS
        #    teachers each pick up one Senior class subject in addition to
        #    their Junior one -- a light, explicit footprint, not pool
        #    sharing.
        # ------------------------------------------------------------------
        jss_cg_ids_early = {cg.id for cg in class_groups if cg_dept_code[id(cg)] is None}
        cross_count = 0
        for jss_code, ss_code in CROSS_CATEGORY_PAIRS:
            jss_pool = subject_pools.get(f"JSS:{jss_code}")
            if not jss_pool:
                continue
            teacher = jss_pool[0]
            # Some codes (ENG, MTH, CIV, AGR, CRS, IRS, YOR) are literally the
            # same subject in both catalogs, so cs_by_cg_and_code has entries
            # for that code from BOTH JSS and SS arms -- explicitly exclude
            # JSS arms here or the "cross" assignment could silently land back
            # on a Junior class subject the teacher already has (a no-op).
            target = next(
                (
                    cs
                    for (cgid, code), cs in cs_by_cg_and_code.items()
                    if code == ss_code and cgid not in jss_cg_ids_early
                ),
                None,
            )
            if target is None:
                continue
            ta = cs_teacher_assignments.get(target.id)
            if ta is None:
                continue
            ta.teacher_id = teacher.id
            cross_count += 1
        await db.flush()
        print(f"Created {cross_count} deliberate JSS<->SS cross-category teacher assignments.")

        # ------------------------------------------------------------------
        # 3.6 Class (form) teachers: every arm gets a homeroom teacher, drawn
        #    from the arm's own already-assigned subject teachers (preferring
        #    the English teacher, as is typical) so it's a real, consistent
        #    person rather than an arbitrary extra hire.
        # ------------------------------------------------------------------
        cs_ids_by_cg: dict[UUID, list[UUID]] = {}
        for (cgid, _code), cs in cs_by_cg_and_code.items():
            cs_ids_by_cg.setdefault(cgid, []).append(cs.id)

        class_teacher_count = 0
        for cg in class_groups:
            teacher_id: UUID | None = None
            eng_cs = cs_by_cg_and_code.get((cg.id, "ENG"))
            if eng_cs is not None:
                ta = cs_teacher_assignments.get(eng_cs.id)
                if ta is not None:
                    teacher_id = ta.teacher_id
            if teacher_id is None:
                for cs_id in cs_ids_by_cg.get(cg.id, []):
                    ta = cs_teacher_assignments.get(cs_id)
                    if ta is not None:
                        teacher_id = ta.teacher_id
                        break
            if teacher_id is not None:
                cg.class_teacher_id = teacher_id
                class_teacher_count += 1
        await db.flush()
        print(f"Assigned class (form) teachers to {class_teacher_count}/{len(class_groups)} class arms.")

        # ------------------------------------------------------------------
        # 4. Joint-class electives: trade subjects & Technical Drawing
        #    combine two arms into one session (low per-arm uptake).
        # ------------------------------------------------------------------
        print(
            "Pairing low-volume trade subjects & Technical Drawing across arms (JOINT_CLASS)...",
            flush=True,
        )
        joint_count = 0
        for lvl_code in ("SS1", "SS2", "SS3"):
            plan = ss_level_arm_plan[lvl_code]
            sci_letters = [letter for letter, d in plan if d == "SCI"]
            art_letters = [letter for letter, d in plan if d == "ART"]
            comm_letters = [letter for letter, d in plan if d == "COMM"]

            pairings: list[tuple[str, list[str]]] = []
            if len(sci_letters) >= 2:
                pairings.append(("TDW", sci_letters[:2]))
            if len(art_letters) >= 2:
                pairings.append(("CAT", art_letters[:2]))
            if comm_letters and art_letters:
                pairings.append(("MKT", [comm_letters[0], art_letters[-1]]))

            for subj_code, letters in pairings:
                cs_ids = []
                for letter in letters:
                    cg = next(
                        (
                            cg
                            for cg in class_groups
                            if cg.academic_level_id == levels_by_code[lvl_code].id
                            and cg.class_arm_id == arms_by_letter[letter].id
                        ),
                        None,
                    )
                    if cg is None:
                        continue
                    cs = cs_by_cg_and_code.get((cg.id, subj_code))
                    if cs is None:
                        # Force-create it for this arm so the pairing is real.
                        dept_code = cg_dept_code[id(cg)]
                        offering = (
                            await db.execute(
                                select(SubjectOffering).where(
                                    SubjectOffering.school_id == school.id,
                                    SubjectOffering.subject_id == subjects_by_code[subj_code].id,
                                    SubjectOffering.academic_level_id
                                    == levels_by_code[lvl_code].id,
                                    SubjectOffering.department_id.in_([depts_by_code[dept_code].id])
                                    if dept_code
                                    else SubjectOffering.department_id.is_(None),
                                )
                            )
                        ).scalar_one_or_none()
                        if offering is None and dept_code:
                            # Fall back to General (department_id=None) offering for school-wide SS subjects.
                            offering = (
                                await db.execute(
                                    select(SubjectOffering).where(
                                        SubjectOffering.school_id == school.id,
                                        SubjectOffering.subject_id
                                        == subjects_by_code[subj_code].id,
                                        SubjectOffering.academic_level_id
                                        == levels_by_code[lvl_code].id,
                                        SubjectOffering.department_id.is_(None),
                                    )
                                )
                            ).scalar_one_or_none()
                        if offering is None:
                            continue
                        cs = ClassSubject(
                            school_id=school.id,
                            subject_offering_id=offering.id,
                            class_group_id=cg.id,
                        )
                        db.add(cs)
                        await db.flush()
                        cs_by_cg_and_code[(cg.id, subj_code)] = cs
                        pool = subject_pools.get(f"SS:{subj_code}")
                        if pool:
                            ta = TeacherAssignment(
                                school_id=school.id,
                                class_subject_id=cs.id,
                                teacher_id=pool[0].id,
                                assigned_by_id=admin.id,
                                is_active=True,
                            )
                            db.add(ta)
                            cs_teacher_assignments[cs.id] = ta
                    cs_ids.append(cs.id)
                if len(cs_ids) >= 2:
                    # All arms in a JOINT_CLASS sync group must share one teacher.
                    first_ta = cs_teacher_assignments.get(cs_ids[0])
                    if first_ta is not None:
                        for csid in cs_ids[1:]:
                            ta = cs_teacher_assignments.get(csid)
                            if ta is not None:
                                ta.teacher_id = first_ta.teacher_id
                    sg = TimetableSyncGroup(
                        school_id=school.id,
                        academic_session_id=session.id,
                        name=f"{lvl_code} {subj_code} Joint Class ({'+'.join(letters)})",
                        sync_type="JOINT_CLASS",
                    )
                    db.add(sg)
                    await db.flush()
                    for csid in cs_ids:
                        db.add(
                            TimetableSyncGroupItem(
                                school_id=school.id, sync_group_id=sg.id, class_subject_id=csid
                            )
                        )
                    joint_count += 1
        await db.flush()
        print(f"Created {joint_count} joint-class sync groups.")

        # ------------------------------------------------------------------
        # 5. Students, enrollments, subject registrations
        # ------------------------------------------------------------------
        print("Seeding students, enrollments, and subject registrations...", flush=True)
        global_elective_codes = {c[0] for c in JSS_SUBJECTS if c[2]} | {
            c[0] for c in SS_SUBJECTS if c[2]
        }
        global_trade_codes = {c[0] for c in SS_SUBJECTS if c[3]}
        jss_cg_ids = {cg.id for cg in class_groups if cg_dept_code[id(cg)] is None}
        cg_elective_codes: dict[UUID, list[str]] = {}
        for cgid, code in cs_by_cg_and_code:
            if cgid in jss_cg_ids and code == "AGR":
                continue
            if code in global_elective_codes:
                cg_elective_codes.setdefault(cgid, []).append(code)

        adm_counter = 1
        total_students = 0
        for cg in class_groups:
            lvl_code = next(
                code for code, lvl in levels_by_code.items() if lvl.id == cg.academic_level_id
            )
            dept_code = cg_dept_code[id(cg)]
            arm_size = rng.randint(8, 15) if dept_code == "COMM" else rng.randint(20, 25)

            for _ in range(arm_size):
                adm_num = f"OBHS/2026/{adm_counter:04d}"
                adm_counter += 1
                total_students += 1
                is_male = rng.choice([True, False])
                fn = naming_rng.choice(MALE_FIRST_NAMES if is_male else FEMALE_FIRST_NAMES)
                ln = naming_rng.choice(LAST_NAMES)
                g_title = naming_rng.choice(GUARDIAN_TITLES_M if not is_male else GUARDIAN_TITLES_F)
                g_fn = naming_rng.choice(MALE_FIRST_NAMES if not is_male else FEMALE_FIRST_NAMES)
                student = Student(
                    school_id=school.id,
                    admission_number=adm_num,
                    first_name=fn,
                    last_name=ln,
                    date_of_birth=_random_dob(rng, lvl_code),
                    gender="M" if is_male else "F",
                    guardian_name=f"{g_title} {g_fn} {ln}",
                    guardian_email=f"{g_fn.lower()}.{ln.lower()}.{adm_counter}@example.com",
                    guardian_phone=f"+23480{rng.randint(10000000, 99999999)}",
                    enrollment_date=today - timedelta(days=40),
                    status="ACTIVE",
                )
                db.add(student)
                await db.flush()

                enrollment = StudentEnrollment(
                    school_id=school.id,
                    student_id=student.id,
                    academic_session_id=session.id,
                    class_group_id=cg.id,
                    enrollment_status="ENROLLED",
                )
                db.add(enrollment)
                await db.flush()

                await auto_register_compulsory_subjects(db, school.id, enrollment, cg, audit)

                # AGR is compulsory in JSS even though Subject.is_elective=True for SS.
                if cg.id in jss_cg_ids and (cg.id, "AGR") in cs_by_cg_and_code:
                    db.add(
                        StudentSubjectRegistration(
                            school_id=school.id,
                            student_enrollment_id=enrollment.id,
                            class_subject_id=cs_by_cg_and_code[(cg.id, "AGR")].id,
                            subject_category="COMPULSORY_CORE",
                        )
                    )

                # A couple of electives per student, from whatever this arm offers.
                elective_codes_here = cg_elective_codes.get(cg.id, [])
                chosen = (
                    rng.sample(elective_codes_here, k=min(2, len(elective_codes_here)))
                    if elective_codes_here
                    else []
                )
                for code in chosen:
                    cs = cs_by_cg_and_code[(cg.id, code)]
                    db.add(
                        StudentSubjectRegistration(
                            school_id=school.id,
                            student_enrollment_id=enrollment.id,
                            class_subject_id=cs.id,
                            subject_category="TRADE_ELECTIVE"
                            if code in global_trade_codes
                            else "FREE_ELECTIVE",
                        )
                    )
        await db.flush()
        print(f"Created {total_students} students across {len(class_groups)} class arms.")

        # ------------------------------------------------------------------
        # 6. Assessment definitions (TESCOM: 6x CA@5% + EXAM@70%) for the term
        # ------------------------------------------------------------------
        await seed_assessment_definitions_for_term(db, school.id, term)
        await db.commit()
        print("Seeded assessment definitions for First Term.")

        # ------------------------------------------------------------------
        # 6.5 Assessment scores: ~96% of (student, component) cells filled in,
        #    as if the term is nearly done -- the remaining ~4% stay unscored
        #    so the UI still has a few gaps to show/fill, same as a real
        #    school at this point in the term. Every scored class subject's
        #    sheet is marked APPROVED so Mark Book/broadsheet/report-card
        #    reads show finished-looking results.
        # ------------------------------------------------------------------
        print("Seeding assessment scores (~96% complete, First Term)...", flush=True)
        SCORE_FILL_RATE = 0.96

        all_defs = (
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
        defs_by_cs: dict[UUID, list[AssessmentDefinition]] = {}
        for defn in all_defs:
            defs_by_cs.setdefault(defn.class_subject_id, []).append(defn)

        reg_rows_for_scoring = (
            await db.execute(
                select(
                    StudentSubjectRegistration.class_subject_id,
                    StudentSubjectRegistration.student_enrollment_id,
                ).where(
                    StudentSubjectRegistration.school_id == school.id,
                    StudentSubjectRegistration.is_active.is_(True),
                )
            )
        ).all()
        enr_ids_by_cs: dict[UUID, list[UUID]] = {}
        for cs_id, enr_id in reg_rows_for_scoring:
            enr_ids_by_cs.setdefault(cs_id, []).append(enr_id)

        score_count = 0
        sheet_count = 0
        now = datetime.now(UTC)
        for cs_id, defs in defs_by_cs.items():
            enr_ids = enr_ids_by_cs.get(cs_id, [])
            if not enr_ids:
                continue
            ta = cs_teacher_assignments.get(cs_id)
            recorder_id = ta.teacher_id if ta is not None else admin.id

            for defn in defs:
                is_exam = defn.code.upper() == "EXAM"
                max_score = float(defn.max_score)
                for enr_id in enr_ids:
                    if rng.random() > SCORE_FILL_RATE:
                        continue
                    if is_exam:
                        raw = rng.triangular(max_score * 0.3, max_score, max_score * 0.72)
                    else:
                        raw = rng.triangular(max_score * 0.4, max_score, max_score * 0.8)
                    raw = min(round(raw * 2) / 2, max_score)
                    db.add(
                        AssessmentScore(
                            school_id=school.id,
                            assessment_definition_id=defn.id,
                            student_enrollment_id=enr_id,
                            score_status="SCORED",
                            raw_score=Decimal(str(raw)),
                            recorded_by_id=recorder_id,
                            last_modified_by_id=recorder_id,
                        )
                    )
                    score_count += 1

            db.add(
                ScoreSheetStatus(
                    school_id=school.id,
                    academic_term_id=term.id,
                    class_subject_id=cs_id,
                    status="APPROVED",
                    submitted_by_id=recorder_id,
                    submitted_at=now,
                    approved_by_id=admin.id,
                    approved_at=now,
                )
            )
            sheet_count += 1
        await db.flush()
        await db.commit()
        print(
            f"Seeded {score_count} assessment scores (~{int(SCORE_FILL_RATE * 100)}% fill) "
            f"across {sheet_count} score sheets, all marked APPROVED."
        )

        # ------------------------------------------------------------------
        # 7. Timetable module constraints: bell schedules only. Rooms,
        #    SubjectSchedulingRules, and the JOINT_CLASS sync groups were
        #    already seeded in steps 3-4 above (so every tab on the
        #    Timetable page -- Bell Schedules, Rules & Sync, Staff/Rooms --
        #    is populated). No Timetable draft or generation run is created
        #    here; that's left for you to trigger from the UI.
        # ------------------------------------------------------------------
        print("Seeding bell schedules...", flush=True)
        jss_bell = BellSchedule(
            school_id=school.id,
            academic_session_id=session.id,
            level_category="JUNIOR_SECONDARY",
            name="Junior Secondary Standard Bell (35m)",
            is_active=True,
        )
        ss_bell = BellSchedule(
            school_id=school.id,
            academic_session_id=session.id,
            level_category="SENIOR_SECONDARY",
            name="Senior Secondary Standard Bell (40m)",
            is_active=True,
        )
        db.add_all([jss_bell, ss_bell])
        await db.flush()

        for slot in _build_week_slots(
            school.id,
            jss_bell.id,
            weekday_slots=JSS_SLOTS,
            thursday_slots=JSS_THURSDAY_SLOTS,
            friday_cutoff=time(12, 45),
            tuesday_club_from=None,
        ):
            db.add(slot)
        for slot in _build_week_slots(
            school.id,
            ss_bell.id,
            weekday_slots=SS_SLOTS,
            thursday_slots=SS_THURSDAY_SLOTS,
            friday_cutoff=time(13, 0),
            tuesday_club_from="Period 7",
        ):
            db.add(slot)
        await db.flush()
        print(
            "Created JUNIOR_SECONDARY and SENIOR_SECONDARY bell schedules (Thu sanitation on both; Tue club time on SS)."
        )

        await db.commit()
        print(
            "Done. Existing staff logins unchanged; new staff/students use password:", DEMO_PASSWORD
        )
        print("Open the Timetable page and click Generate Timetable to produce the schedule.")


if __name__ == "__main__":
    asyncio.run(seed())
