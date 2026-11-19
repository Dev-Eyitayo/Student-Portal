from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    ConflictException,
    InsufficientPermissionException,
    ResourceNotFoundException,
    ValidationException,
)
from app.core.permissions import build_scope_filter, get_permission_scope, require_scoped_permission
from app.modules.academic_structure.models import AcademicSession, ClassGroup
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.common.models import generate_uuid7
from app.modules.common.pagination import paginate
from app.modules.common.schemas import PageMeta
from app.modules.identity.models import User
from app.modules.students.bulk_import import ParsedStudentRow
from app.modules.students.models import Student, StudentEnrollment
from app.modules.students.schemas import (
    EnrollmentCreateRequest,
    EnrollmentUpdateRequest,
    StudentCreateRequest,
    StudentStatusUpdateRequest,
    StudentUpdateRequest,
)
from app.modules.subjects.services import auto_register_compulsory_subjects

ALLOWED_STUDENT_STATUSES = {
    "ADMITTED",
    "ACTIVE",
    "SUSPENDED",
    "WITHDRAWN",
    "TRANSFERRED",
    "GRADUATED",
}
ARCHIVED_STUDENT_STATUSES = {"WITHDRAWN", "TRANSFERRED"}
STUDENT_STATUS_TRANSITIONS = {
    "ADMITTED": {"ACTIVE", "WITHDRAWN"},
    "ACTIVE": {"SUSPENDED", "WITHDRAWN", "TRANSFERRED", "GRADUATED"},
    "SUSPENDED": {"ACTIVE", "WITHDRAWN", "TRANSFERRED"},
    "WITHDRAWN": set(),
    "TRANSFERRED": set(),
    "GRADUATED": set(),
}
ALLOWED_ENROLLMENT_STATUSES = {
    "ENROLLED",
    "PROMOTED",
    "REPEATING",
    "TRANSFERRED",
    "WITHDRAWN",
}
ENROLLMENT_STATUS_TRANSITIONS = {
    "ENROLLED": {"PROMOTED", "REPEATING", "TRANSFERRED", "WITHDRAWN"},
    "PROMOTED": set(),
    "REPEATING": set(),
    "TRANSFERRED": set(),
    "WITHDRAWN": set(),
}


async def _commit_or_conflict(
    db: AsyncSession,
    *,
    message: str,
    code: str = "STUDENT_CONFLICT",
) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictException(message=message, code=code) from exc


def _normalize_gender(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"M", "MALE"}:
        return "M"
    if normalized in {"F", "FEMALE"}:
        return "F"
    raise ValidationException("Please enter a valid gender: M for male or F for female.")


def _normalize_admission_number(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValidationException("Please enter the student's admission number.")
    return normalized


def _validate_student_dates(
    date_of_birth: date,
    enrollment_date: date,
) -> None:
    if date_of_birth >= enrollment_date:
        raise ValidationException(
            "The student's date of birth must be before their enrollment date."
        )
    if date_of_birth > date.today():
        raise ValidationException(
            "The student's date of birth cannot be in the future. Please check the date and try again."
        )


async def _ensure_admission_available(
    db: AsyncSession,
    school_id: UUID,
    admission_number: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    stmt = select(Student.id).where(
        Student.school_id == school_id,
        Student.admission_number == admission_number,
    )
    if exclude_id is not None:
        stmt = stmt.where(Student.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise ConflictException(
            message=(
                f"The admission number '{admission_number}' is already in use in your school. "
                "Please check the number or open the existing student record."
            ),
            code="ADMISSION_NUMBER_TAKEN",
        )


def _student_query(
    school_id: UUID,
    *,
    include_archived: bool,
    status: str | None,
    q: str | None,
    gender: str | None = None,
    academic_level_id: UUID | None = None,
    class_group_id: UUID | None = None,
) -> Select[tuple[Student]]:
    stmt = select(Student).where(Student.school_id == school_id)
    if not include_archived:
        stmt = stmt.where(Student.deleted_at.is_(None))
    if status:
        stmt = stmt.where(Student.status == status)
    if gender:
        stmt = stmt.where(Student.gender == _normalize_gender(gender))
    if academic_level_id is not None or class_group_id is not None:
        enroll_sub = (
            select(StudentEnrollment.student_id)
            .join(ClassGroup, ClassGroup.id == StudentEnrollment.class_group_id)
            .where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.deleted_at.is_(None),
                StudentEnrollment.enrollment_status == "ENROLLED",
            )
        )
        if class_group_id is not None:
            enroll_sub = enroll_sub.where(StudentEnrollment.class_group_id == class_group_id)
        if academic_level_id is not None:
            enroll_sub = enroll_sub.where(ClassGroup.academic_level_id == academic_level_id)
        stmt = stmt.where(Student.id.in_(enroll_sub))
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Student.admission_number.ilike(pattern),
                Student.first_name.ilike(pattern),
                Student.middle_name.ilike(pattern),
                Student.last_name.ilike(pattern),
            )
        )
    return stmt.order_by(Student.last_name.asc(), Student.first_name.asc())


async def _get_student(
    db: AsyncSession,
    school_id: UUID,
    student_id: UUID,
) -> Student:
    stmt = select(Student).where(
        Student.id == student_id,
        Student.school_id == school_id,
    )
    student = (await db.execute(stmt)).scalar_one_or_none()
    if not student:
        raise ResourceNotFoundException("student", student_id)
    return student


def _enrollment_query(school_id: UUID) -> Select[tuple[StudentEnrollment]]:
    return (
        select(StudentEnrollment)
        .options(
            selectinload(StudentEnrollment.class_group),
            selectinload(StudentEnrollment.academic_session),
        )
        .where(StudentEnrollment.school_id == school_id)
    )


async def _get_enrollment(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
) -> StudentEnrollment:
    stmt = _enrollment_query(school_id).where(StudentEnrollment.id == enrollment_id)
    enrollment = (await db.execute(stmt)).scalar_one_or_none()
    if not enrollment:
        raise ResourceNotFoundException("student enrollment", enrollment_id)
    return enrollment


async def _get_active_class_group(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
) -> ClassGroup:
    stmt = (
        select(ClassGroup)
        .options(selectinload(ClassGroup.academic_session))
        .where(
            ClassGroup.id == class_group_id,
            ClassGroup.school_id == school_id,
            ClassGroup.deleted_at.is_(None),
        )
    )
    class_group = (await db.execute(stmt)).scalar_one_or_none()
    if not class_group:
        raise ResourceNotFoundException("class group", class_group_id)
    if class_group.academic_session.deleted_at is not None:
        raise ValidationException(
            "This class group belongs to a retired academic session, so students cannot be "
            "enrolled into it."
        )
    return class_group


async def _enrollment_exists(
    db: AsyncSession,
    student_id: UUID,
    academic_session_id: UUID,
) -> bool:
    stmt = select(StudentEnrollment.id).where(
        StudentEnrollment.student_id == student_id,
        StudentEnrollment.academic_session_id == academic_session_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def list_students(
    db: AsyncSession,
    school_id: UUID,
    *,
    page: int,
    page_size: int,
    q: str | None,
    status: str | None,
    include_archived: bool,
    gender: str | None = None,
    academic_level_id: UUID | None = None,
    class_group_id: UUID | None = None,
) -> tuple[list[Student], PageMeta]:
    normalized_status = status.strip().upper() if status else None
    stmt = _student_query(
        school_id,
        include_archived=include_archived,
        status=normalized_status,
        q=q,
        gender=gender,
        academic_level_id=academic_level_id,
        class_group_id=class_group_id,
    )
    items, meta = await paginate(db, stmt, page=page, page_size=page_size)
    if items:
        student_ids = [s.id for s in items]
        enroll_rows = (
            await db.execute(
                select(
                    StudentEnrollment.student_id,
                    ClassGroup.id,
                    ClassGroup.display_name,
                    AcademicSession.is_current,
                )
                .join(ClassGroup, ClassGroup.id == StudentEnrollment.class_group_id)
                .join(AcademicSession, AcademicSession.id == StudentEnrollment.academic_session_id)
                .where(
                    StudentEnrollment.school_id == school_id,
                    StudentEnrollment.student_id.in_(student_ids),
                    StudentEnrollment.enrollment_status == "ENROLLED",
                    StudentEnrollment.deleted_at.is_(None),
                )
                .order_by(
                    AcademicSession.is_current.desc(),
                    StudentEnrollment.created_at.desc(),
                )
            )
        ).all()
        class_map: dict[UUID, tuple[UUID, str]] = {}
        for sid, cg_id, cg_name, _ in enroll_rows:
            if sid not in class_map:
                class_map[sid] = (cg_id, cg_name)
        for s in items:
            match = class_map.get(s.id)
            s.current_class_group_id = match[0] if match else None
            s.current_class_name = match[1] if match else None
    return items, meta


async def get_students_summary(db: AsyncSession, school_id: UUID) -> dict[str, int]:
    from sqlalchemy import case

    summary_row = (
        await db.execute(
            select(
                func.count(Student.id),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Student.status.in_(("WITHDRAWN", "GRADUATED", "TRANSFERRED")),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (Student.status.in_(("ACTIVE", "ADMITTED")), 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(
                Student.school_id == school_id,
                Student.deleted_at.is_(None),
            )
        )
    ).one()

    total = int(summary_row[0] or 0)
    withdrawn_or_graduated = int(summary_row[1] or 0)
    active_or_admitted = int(summary_row[2] or 0)

    current_session_id = (
        await db.execute(
            select(AcademicSession.id).where(
                AcademicSession.school_id == school_id,
                AcademicSession.is_current.is_(True),
                AcademicSession.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    enrolled_current = 0
    if current_session_id is not None:
        enrolled_current = int(
            (
                await db.execute(
                    select(func.count(func.distinct(StudentEnrollment.student_id)))
                    .join(Student, Student.id == StudentEnrollment.student_id)
                    .where(
                        StudentEnrollment.school_id == school_id,
                        StudentEnrollment.academic_session_id == current_session_id,
                        StudentEnrollment.enrollment_status == "ENROLLED",
                        StudentEnrollment.deleted_at.is_(None),
                        Student.deleted_at.is_(None),
                        Student.status.in_(("ACTIVE", "ADMITTED")),
                    )
                )
            ).scalar_one()
            or 0
        )

    unenrolled = max(0, active_or_admitted - enrolled_current)
    return {
        "total": total,
        "enrolled_current_session": enrolled_current,
        "unenrolled": unenrolled,
        "withdrawn_or_graduated": withdrawn_or_graduated,
    }


async def create_student(
    db: AsyncSession,
    school_id: UUID,
    req: StudentCreateRequest,
    audit: AuditContext,
) -> Student:
    admission_number = _normalize_admission_number(req.admission_number)
    gender = _normalize_gender(req.gender)
    _validate_student_dates(req.date_of_birth, req.enrollment_date)
    await _ensure_admission_available(db, school_id, admission_number)

    student = Student(
        school_id=school_id,
        admission_number=admission_number,
        first_name=req.first_name.strip(),
        middle_name=req.middle_name.strip() if req.middle_name else None,
        last_name=req.last_name.strip(),
        date_of_birth=req.date_of_birth,
        gender=gender,
        guardian_name=req.guardian_name.strip(),
        guardian_email=str(req.guardian_email).lower(),
        guardian_phone=req.guardian_phone.strip(),
        enrollment_date=req.enrollment_date,
        status="ADMITTED",
    )
    db.add(student)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="STUDENT_CREATED",
        resource_type="Student",
        resource_id=student.id,
        diff_payload={
            "after": {
                "admission_number": admission_number,
                "status": "ADMITTED",
                "enrollment_date": req.enrollment_date,
            }
        },
    )
    await _commit_or_conflict(
        db,
        message=(
            "This student record could not be saved because the admission number is already in use."
        ),
        code="ADMISSION_NUMBER_TAKEN",
    )
    await db.refresh(student)
    return student


async def get_student(db: AsyncSession, school_id: UUID, student_id: UUID) -> Student:
    return await _get_student(db, school_id, student_id)


async def update_student(
    db: AsyncSession,
    school_id: UUID,
    student_id: UUID,
    req: StudentUpdateRequest,
    audit: AuditContext,
) -> Student:
    student = await _get_student(db, school_id, student_id)
    changes = req.model_dump(exclude_unset=True)
    fields_set = req.model_fields_set

    if "first_name" in fields_set and req.first_name is not None:
        student.first_name = req.first_name.strip()
    if "middle_name" in fields_set:
        student.middle_name = req.middle_name.strip() if req.middle_name else None
    if "last_name" in fields_set and req.last_name is not None:
        student.last_name = req.last_name.strip()
    if "date_of_birth" in fields_set and req.date_of_birth is not None:
        student.date_of_birth = req.date_of_birth
    if "gender" in fields_set and req.gender is not None:
        student.gender = _normalize_gender(req.gender)
    if "guardian_name" in fields_set and req.guardian_name is not None:
        student.guardian_name = req.guardian_name.strip()
    if "guardian_email" in fields_set and req.guardian_email is not None:
        student.guardian_email = str(req.guardian_email).lower()
    if "guardian_phone" in fields_set and req.guardian_phone is not None:
        student.guardian_phone = req.guardian_phone.strip()
    if "enrollment_date" in fields_set and req.enrollment_date is not None:
        student.enrollment_date = req.enrollment_date

    _validate_student_dates(student.date_of_birth, student.enrollment_date)

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="STUDENT_UPDATED",
        resource_type="Student",
        resource_id=student.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This student record could not be saved. Please try again.",
        code="STUDENT_CONFLICT",
    )
    await db.refresh(student)
    return student


async def update_student_status(
    db: AsyncSession,
    school_id: UUID,
    student_id: UUID,
    req: StudentStatusUpdateRequest,
    audit: AuditContext,
) -> Student:
    student = await _get_student(db, school_id, student_id)
    target_status = req.status.strip().upper()
    if target_status not in ALLOWED_STUDENT_STATUSES:
        raise ValidationException(
            "Please choose a valid student status: ADMITTED, ACTIVE, SUSPENDED, WITHDRAWN, "
            "TRANSFERRED, or GRADUATED."
        )
    if target_status == student.status:
        raise ValidationException("The student is already in this status.")
    if target_status not in STUDENT_STATUS_TRANSITIONS[student.status]:
        raise ValidationException(
            f"A student who is currently {student.status.lower()} cannot be changed to "
            f"{target_status.lower()}. Please choose a valid next status."
        )

    previous_status = student.status
    student.status = target_status
    if target_status in ARCHIVED_STUDENT_STATUSES:
        student.deleted_at = datetime.now(UTC)

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="STUDENT_STATUS_CHANGED",
        resource_type="Student",
        resource_id=student.id,
        diff_payload={
            "before": {"status": previous_status},
            "after": {"status": target_status},
            "reason": req.reason.strip(),
        },
    )
    await _commit_or_conflict(
        db,
        message="This student's status could not be updated. Please try again.",
        code="STUDENT_STATUS_CONFLICT",
    )
    await db.refresh(student)
    return student


async def list_student_enrollments(
    db: AsyncSession,
    school_id: UUID,
    student_id: UUID,
    current_user: User,
    *,
    page: int,
    page_size: int,
) -> tuple[list[StudentEnrollment], PageMeta]:
    await _get_student(db, school_id, student_id)
    scope = await get_permission_scope(db, current_user, "students:enroll")
    if not scope.has_any_access:
        raise InsufficientPermissionException(permission_code="students:enroll")

    stmt = (
        _enrollment_query(school_id)
        .join(
            AcademicSession,
            AcademicSession.id == StudentEnrollment.academic_session_id,
        )
        .where(StudentEnrollment.student_id == student_id)
        .order_by(AcademicSession.start_date.desc())
    )
    if not scope.unscoped:
        stmt = stmt.join(ClassGroup, ClassGroup.id == StudentEnrollment.class_group_id).where(
            build_scope_filter(
                scope,
                department_column=ClassGroup.department_id,
                academic_level_column=ClassGroup.academic_level_id,
                class_group_column=ClassGroup.id,
            )
        )
    return await paginate(db, stmt, page=page, page_size=page_size)


async def create_enrollment(
    db: AsyncSession,
    school_id: UUID,
    student_id: UUID,
    req: EnrollmentCreateRequest,
    current_user: User,
    audit: AuditContext,
) -> StudentEnrollment:
    student = await _get_student(db, school_id, student_id)
    if student.deleted_at is not None or student.status not in {"ADMITTED", "ACTIVE"}:
        raise ValidationException(
            "Only admitted or active students can be enrolled. Please check the student's "
            "status before trying again."
        )

    class_group = await _get_active_class_group(db, school_id, req.class_group_id)
    await require_scoped_permission(
        db,
        current_user,
        "students:enroll",
        department_id=class_group.department_id,
        academic_level_id=class_group.academic_level_id,
        class_group_id=class_group.id,
    )
    if await _enrollment_exists(db, student.id, class_group.academic_session_id):
        raise ConflictException(
            message=(
                "This student is already enrolled for this academic session. Please edit the "
                "existing enrollment instead."
            ),
            code="STUDENT_ALREADY_ENROLLED",
        )

    enrollment = StudentEnrollment(
        school_id=school_id,
        student_id=student.id,
        academic_session_id=class_group.academic_session_id,
        class_group_id=class_group.id,
        enrollment_status="ENROLLED",
    )
    if student.status == "ADMITTED":
        student.status = "ACTIVE"
    db.add(enrollment)
    await db.flush()
    await auto_register_compulsory_subjects(db, school_id, enrollment, class_group, audit)
    record_audit_log(
        db,
        audit,
        action="STUDENT_ENROLLED",
        resource_type="StudentEnrollment",
        resource_id=enrollment.id,
        diff_payload={
            "after": {
                "student_id": student.id,
                "class_group_id": class_group.id,
                "academic_session_id": class_group.academic_session_id,
                "enrollment_status": "ENROLLED",
            }
        },
    )
    await _commit_or_conflict(
        db,
        message=(
            "This student could not be enrolled because an enrollment already exists for this "
            "academic session."
        ),
        code="STUDENT_ALREADY_ENROLLED",
    )
    return await _get_enrollment(db, school_id, enrollment.id)


async def _require_enrollment_scope(
    db: AsyncSession,
    current_user: User,
    enrollment: StudentEnrollment,
    permission_code: str,
) -> None:
    await require_scoped_permission(
        db,
        current_user,
        permission_code,
        department_id=enrollment.class_group.department_id,
        academic_level_id=enrollment.class_group.academic_level_id,
        class_group_id=enrollment.class_group_id,
    )


async def get_enrollment(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    current_user: User,
) -> StudentEnrollment:
    enrollment = await _get_enrollment(db, school_id, enrollment_id)
    await _require_enrollment_scope(db, current_user, enrollment, "students:enroll")
    return enrollment


async def update_enrollment(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    req: EnrollmentUpdateRequest,
    current_user: User,
    audit: AuditContext,
) -> StudentEnrollment:
    enrollment = await _get_enrollment(db, school_id, enrollment_id)
    await _require_enrollment_scope(db, current_user, enrollment, "students:enroll")
    target_status = req.enrollment_status.strip().upper()
    if target_status not in ALLOWED_ENROLLMENT_STATUSES:
        raise ValidationException(
            "Please choose a valid enrollment status: ENROLLED, PROMOTED, REPEATING, "
            "TRANSFERRED, or WITHDRAWN."
        )
    if target_status == enrollment.enrollment_status:
        raise ValidationException("This enrollment is already in the selected status.")
    if target_status not in ENROLLMENT_STATUS_TRANSITIONS[enrollment.enrollment_status]:
        raise ValidationException(
            f"An enrollment that is currently {enrollment.enrollment_status.lower()} cannot "
            f"be changed to {target_status.lower()}. Please choose a valid next status."
        )

    previous_status = enrollment.enrollment_status
    promoted_to_class_group_id: UUID | None = None

    if target_status in {"PROMOTED", "REPEATING"}:
        if target_status == "PROMOTED" and req.promoted_to_class_group_id is None:
            raise ValidationException(
                "Please select the class group the student is being promoted into."
            )
        if req.promoted_to_class_group_id is not None:
            target_class_group = await _get_active_class_group(
                db,
                school_id,
                req.promoted_to_class_group_id,
            )
            if (
                target_class_group.academic_session.start_date
                <= enrollment.academic_session.start_date
            ):
                raise ValidationException(
                    "The promotion class must belong to a later academic session than the "
                    "student's current enrollment."
                )
            promoted_to_class_group_id = target_class_group.id

    enrollment.promoted_to_class_group_id = promoted_to_class_group_id
    enrollment.enrollment_status = target_status

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ENROLLMENT_STATUS_CHANGED",
        resource_type="StudentEnrollment",
        resource_id=enrollment.id,
        diff_payload={
            "before": {"enrollment_status": previous_status},
            "after": {
                "enrollment_status": target_status,
                "promoted_to_class_group_id": promoted_to_class_group_id,
            },
            "reason": req.reason.strip() if req.reason else None,
        },
    )
    await _commit_or_conflict(
        db,
        message="This enrollment could not be updated. Please try again.",
        code="ENROLLMENT_CONFLICT",
    )
    return await _get_enrollment(db, school_id, enrollment.id)


@dataclass
class StudentBulkProvisionResult:
    created: int
    errors: list[dict[str, Any]] = field(default_factory=list)


async def provision_student_batch(
    db: AsyncSession,
    school_id: UUID,
    rows: list[ParsedStudentRow],
    audit: AuditContext,
) -> StudentBulkProvisionResult:
    """Validates and creates an imported student roster atomically."""
    errors: list[dict[str, Any]] = []
    seen_admissions: dict[str, int] = {}

    for parsed in rows:
        admission_number = parsed.data.admission_number
        if admission_number in seen_admissions:
            errors.append(
                {
                    "row": parsed.row_number,
                    "message": (
                        f"The admission number {admission_number} appears more than once in "
                        f"this file. It was first used on row {seen_admissions[admission_number]}."
                    ),
                }
            )
        else:
            seen_admissions[admission_number] = parsed.row_number

        try:
            _validate_student_dates(parsed.data.date_of_birth, parsed.data.enrollment_date)
        except ValidationException as exc:
            errors.append({"row": parsed.row_number, "message": exc.message})

    existing_admissions = set(
        (
            await db.execute(
                select(Student.admission_number).where(
                    Student.school_id == school_id,
                    Student.admission_number.in_(seen_admissions),
                )
            )
        )
        .scalars()
        .all()
    )

    for parsed in rows:
        admission_number = parsed.data.admission_number
        if admission_number in existing_admissions:
            errors.append(
                {
                    "row": parsed.row_number,
                    "message": (
                        f"A student with admission number {admission_number} already exists "
                        "in your school. Please check the number and try again."
                    ),
                }
            )

    if errors:
        return StudentBulkProvisionResult(created=0, errors=errors)

    try:
        students: list[Student] = []
        for parsed in rows:
            row = parsed.data
            students.append(
                Student(
                    school_id=school_id,
                    admission_number=row.admission_number,
                    first_name=row.first_name,
                    middle_name=row.middle_name,
                    last_name=row.last_name,
                    date_of_birth=row.date_of_birth,
                    gender=row.gender,
                    guardian_name=row.guardian_name,
                    guardian_email=str(row.guardian_email).lower(),
                    guardian_phone=row.guardian_phone,
                    enrollment_date=row.enrollment_date,
                    status="ADMITTED",
                )
            )

        db.add_all(students)
        await db.flush()
        record_audit_log(
            db,
            audit,
            action="STUDENT_BULK_IMPORTED",
            resource_type="StudentBulkImport",
            resource_id=generate_uuid7(),
            diff_payload={"created": len(students)},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return StudentBulkProvisionResult(
            created=0,
            errors=[
                {
                    "row": 0,
                    "message": (
                        "We couldn't save this student list because one or more details "
                        "conflict with existing records. Please review the file and try again."
                    ),
                }
            ],
        )

    return StudentBulkProvisionResult(created=len(students), errors=[])
