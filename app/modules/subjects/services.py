from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    InsufficientPermissionException,
    ResourceNotFoundException,
    ValidationException,
)
from app.core.permissions import build_scope_filter, get_permission_scope, require_scoped_permission
from app.modules.academic_structure.models import (
    AcademicLevel,
    AcademicSession,
    ClassGroup,
    Department,
)
from app.modules.assessments.services import seed_assessment_definitions_for_class_subject
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.common.pagination import paginate
from app.modules.common.schemas import PageMeta
from app.modules.identity.models import StaffProfile, User
from app.modules.students.models import StudentEnrollment
from app.modules.subjects.models import (
    ClassSubject,
    StudentSubjectRegistration,
    Subject,
    SubjectOffering,
    TeacherAssignment,
    TeacherSubjectQualification,
)
from app.modules.subjects.schemas import (
    ClassGroupSubjectItem,
    ClassSubjectCreateRequest,
    ClassSubjectTeacherSummary,
    MyTeachingAssignmentResponse,
    OfferingCreateRequest,
    OfferingUpdateRequest,
    QualifiedTeacherResponse,
    SubjectCreateRequest,
    SubjectUpdateRequest,
    TeacherAssignmentBulkCreateRequest,
    TeacherAssignmentBulkSkip,
    TeacherSubjectQualificationsResponse,
)

ASSIGNABLE_TEACHER_ROLES = {"TEACHER", "ACADEMIC_OFFICER", "SCHOOL_ADMIN"}
AUTO_REGISTER_TRADE_SUBJECT = "TRADE_ELECTIVE"
AUTO_REGISTER_FREE_ELECTIVE = "FREE_ELECTIVE"


async def _commit_or_conflict(
    db: AsyncSession,
    *,
    message: str,
    code: str = "SUBJECT_CONFLICT",
) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictException(message=message, code=code) from exc


async def _ensure_subject_code_available(
    db: AsyncSession,
    school_id: UUID,
    code: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    stmt = select(Subject.id).where(
        Subject.school_id == school_id,
        Subject.deleted_at.is_(None),
        Subject.code == code,
    )
    if exclude_id is not None:
        stmt = stmt.where(Subject.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise ConflictException(
            message=(
                "A subject with this code already exists. Please choose a different code or "
                "edit the existing subject."
            ),
            code="SUBJECT_CODE_TAKEN",
        )


async def _get_subject(
    db: AsyncSession,
    school_id: UUID,
    subject_id: UUID,
    *,
    include_retired: bool = False,
) -> Subject:
    stmt = select(Subject).where(Subject.id == subject_id, Subject.school_id == school_id)
    if not include_retired:
        stmt = stmt.where(Subject.deleted_at.is_(None))
    subject = (await db.execute(stmt)).scalar_one_or_none()
    if not subject:
        raise ResourceNotFoundException("subject", subject_id)
    return subject


async def _get_offering(
    db: AsyncSession,
    school_id: UUID,
    offering_id: UUID,
    *,
    include_retired: bool = False,
) -> SubjectOffering:
    stmt = select(SubjectOffering).where(
        SubjectOffering.id == offering_id, SubjectOffering.school_id == school_id
    )
    if not include_retired:
        stmt = stmt.where(SubjectOffering.deleted_at.is_(None))
    offering = (await db.execute(stmt)).scalar_one_or_none()
    if not offering:
        raise ResourceNotFoundException("subject offering", offering_id)
    return offering


async def _get_class_subject(
    db: AsyncSession,
    school_id: UUID,
    class_subject_id: UUID,
    *,
    include_retired: bool = False,
) -> ClassSubject:
    stmt = select(ClassSubject).where(
        ClassSubject.id == class_subject_id, ClassSubject.school_id == school_id
    )
    if not include_retired:
        stmt = stmt.where(ClassSubject.deleted_at.is_(None))
    class_subject = (await db.execute(stmt)).scalar_one_or_none()
    if not class_subject:
        raise ResourceNotFoundException("class subject", class_subject_id)
    return class_subject


async def _get_active_class_group(
    db: AsyncSession, school_id: UUID, class_group_id: UUID
) -> ClassGroup:
    stmt = select(ClassGroup).where(
        ClassGroup.id == class_group_id,
        ClassGroup.school_id == school_id,
        ClassGroup.deleted_at.is_(None),
    )
    class_group = (await db.execute(stmt)).scalar_one_or_none()
    if not class_group:
        raise ResourceNotFoundException("class group", class_group_id)
    return class_group


async def _resolve_class_group_scope(db: AsyncSession, class_group_id: UUID) -> ClassGroup:
    stmt = select(ClassGroup).where(ClassGroup.id == class_group_id)
    return (await db.execute(stmt)).scalar_one()


async def _require_class_subject_scope(
    db: AsyncSession,
    current_user: User,
    class_subject: ClassSubject,
    permission_code: str,
) -> None:
    class_group = await _resolve_class_group_scope(db, class_subject.class_group_id)
    await require_scoped_permission(
        db,
        current_user,
        permission_code,
        department_id=class_group.department_id,
        academic_level_id=class_group.academic_level_id,
        class_group_id=class_group.id,
    )


async def _require_enrollment_scope(
    db: AsyncSession,
    current_user: User,
    enrollment: StudentEnrollment,
    permission_code: str,
) -> None:
    class_group = await _resolve_class_group_scope(db, enrollment.class_group_id)
    await require_scoped_permission(
        db,
        current_user,
        permission_code,
        department_id=class_group.department_id,
        academic_level_id=class_group.academic_level_id,
        class_group_id=class_group.id,
    )


async def _validate_teacher(db: AsyncSession, school_id: UUID, teacher_id: UUID) -> User:
    stmt = select(User).where(
        User.id == teacher_id,
        User.school_id == school_id,
        User.is_active.is_(True),
    )
    teacher = (await db.execute(stmt)).scalar_one_or_none()
    if not teacher:
        raise ValidationException(
            "We couldn't find an active staff member with this ID in your school. Please choose "
            "a different teacher."
        )
    if teacher.role not in ASSIGNABLE_TEACHER_ROLES:
        raise ValidationException(
            "This staff member cannot be assigned to teach a subject. Please choose a teacher, "
            "academic officer, or school administrator."
        )
    return teacher


# --------------------------------------------------------------------------
# Subjects
# --------------------------------------------------------------------------


async def list_subjects(
    db: AsyncSession,
    school_id: UUID,
    *,
    page: int,
    page_size: int,
    include_retired: bool,
) -> tuple[list[Subject], PageMeta]:
    stmt = select(Subject).where(Subject.school_id == school_id)
    if not include_retired:
        stmt = stmt.where(Subject.deleted_at.is_(None))
    stmt = stmt.order_by(Subject.name.asc())
    return await paginate(db, stmt, page=page, page_size=page_size)


async def create_subject(
    db: AsyncSession,
    school_id: UUID,
    req: SubjectCreateRequest,
    audit: AuditContext,
) -> Subject:
    code = req.code.strip().upper()
    await _ensure_subject_code_available(db, school_id, code)
    subject = Subject(
        school_id=school_id,
        name=req.name.strip(),
        code=code,
        is_elective=req.is_elective,
        is_trade_subject=req.is_trade_subject,
    )
    db.add(subject)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SUBJECT_CREATED",
        resource_type="Subject",
        resource_id=subject.id,
        diff_payload={"after": {"name": subject.name, "code": code}},
    )
    await _commit_or_conflict(
        db,
        message="A subject with this code already exists. Please choose another code.",
        code="SUBJECT_CODE_TAKEN",
    )
    await db.refresh(subject)
    return subject


async def get_subject(db: AsyncSession, school_id: UUID, subject_id: UUID) -> Subject:
    return await _get_subject(db, school_id, subject_id)


async def update_subject(
    db: AsyncSession,
    school_id: UUID,
    subject_id: UUID,
    req: SubjectUpdateRequest,
    audit: AuditContext,
) -> Subject:
    subject = await _get_subject(db, school_id, subject_id)
    changes = req.model_dump(exclude_unset=True)

    if req.code is not None:
        code = req.code.strip().upper()
        await _ensure_subject_code_available(db, school_id, code, exclude_id=subject.id)
        subject.code = code
    if req.name is not None:
        subject.name = req.name.strip()
    if req.is_elective is not None:
        subject.is_elective = req.is_elective
    if req.is_trade_subject is not None:
        subject.is_trade_subject = req.is_trade_subject

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SUBJECT_UPDATED",
        resource_type="Subject",
        resource_id=subject.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This subject could not be saved because its code conflicts with another subject.",
        code="SUBJECT_CODE_TAKEN",
    )
    await db.refresh(subject)
    return subject


async def retire_subject(
    db: AsyncSession,
    school_id: UUID,
    subject_id: UUID,
    audit: AuditContext,
) -> Subject:
    subject = await _get_subject(db, school_id, subject_id)
    subject.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SUBJECT_RETIRED",
        resource_type="Subject",
        resource_id=subject.id,
        diff_payload={"deleted_at": subject.deleted_at},
    )
    await _commit_or_conflict(
        db,
        message="This subject could not be retired. Please try again.",
        code="SUBJECT_RETIRE_CONFLICT",
    )
    await db.refresh(subject)
    return subject


# --------------------------------------------------------------------------
# Subject Offerings
# --------------------------------------------------------------------------


def _offering_query(
    school_id: UUID,
    *,
    subject_id: UUID | None,
    academic_session_id: UUID | None,
    academic_level_id: UUID | None,
    include_retired: bool,
) -> Select[tuple[SubjectOffering]]:
    stmt = select(SubjectOffering).where(SubjectOffering.school_id == school_id)
    if not include_retired:
        stmt = stmt.where(SubjectOffering.deleted_at.is_(None))
    if subject_id is not None:
        stmt = stmt.where(SubjectOffering.subject_id == subject_id)
    if academic_session_id is not None:
        stmt = stmt.where(SubjectOffering.academic_session_id == academic_session_id)
    if academic_level_id is not None:
        stmt = stmt.where(SubjectOffering.academic_level_id == academic_level_id)
    return stmt.order_by(SubjectOffering.created_at.desc(), SubjectOffering.id.desc())


async def list_offerings(
    db: AsyncSession,
    school_id: UUID,
    current_user: User,
    *,
    subject_id: UUID | None,
    academic_session_id: UUID | None,
    academic_level_id: UUID | None,
    page: int,
    page_size: int,
    include_retired: bool,
) -> tuple[list[SubjectOffering], PageMeta]:
    scope = await get_permission_scope(db, current_user, "subjects:manage")
    if not scope.has_any_access:
        raise InsufficientPermissionException(permission_code="subjects:manage")

    stmt = _offering_query(
        school_id,
        subject_id=subject_id,
        academic_session_id=academic_session_id,
        academic_level_id=academic_level_id,
        include_retired=include_retired,
    )
    if not scope.unscoped:
        stmt = stmt.where(
            build_scope_filter(
                scope,
                department_column=SubjectOffering.department_id,
                academic_level_column=SubjectOffering.academic_level_id,
            )
        )
    items, meta = await paginate(db, stmt, page=page, page_size=page_size)
    if items:
        subj_ids = list({o.subject_id for o in items})
        level_ids = list({o.academic_level_id for o in items})
        dept_ids = list({o.department_id for o in items if o.department_id is not None})

        subj_map = {
            sid: (name, code)
            for sid, name, code in (
                await db.execute(
                    select(Subject.id, Subject.name, Subject.code).where(Subject.id.in_(subj_ids))
                )
            ).all()
        }
        level_map = dict(
            (
                await db.execute(
                    select(AcademicLevel.id, AcademicLevel.name).where(
                        AcademicLevel.id.in_(level_ids)
                    )
                )
            ).all()
        )
        dept_map: dict[UUID, str] = {}
        if dept_ids:
            dept_map = dict(
                (
                    await db.execute(
                        select(Department.id, Department.name).where(Department.id.in_(dept_ids))
                    )
                ).all()
            )
        for o in items:
            s_info = subj_map.get(o.subject_id)
            if s_info:
                o.subject_name, o.subject_code = s_info
            o.academic_level_name = level_map.get(o.academic_level_id)
            o.department_name = dept_map.get(o.department_id) if o.department_id else None
    return items, meta


async def _validate_offering_department(
    db: AsyncSession,
    school_id: UUID,
    level: AcademicLevel,
    department_id: UUID | None,
) -> Department | None:
    if department_id is None:
        return None
    if level.category in ("JUNIOR_SECONDARY", "PRIMARY"):
        level_label = "Primary" if level.category == "PRIMARY" else "Junior secondary"
        raise ValidationException(
            f"{level_label} subject offerings do not use a department. Please leave the "
            "department empty for this offering."
        )
    stmt = select(Department).where(
        Department.id == department_id,
        Department.school_id == school_id,
        Department.deleted_at.is_(None),
    )
    department = (await db.execute(stmt)).scalar_one_or_none()
    if not department:
        raise ResourceNotFoundException("department", department_id)
    return department


async def _validate_offering_scope_exclusivity(
    db: AsyncSession,
    school_id: UUID,
    subject_id: UUID,
    session_id: UUID,
    level_id: UUID,
    department_id: UUID | None,
    exclude_offering_id: UUID | None = None,
) -> None:
    """Prevent mixing a General (department=NULL) offering with department-specific ones."""
    stmt = select(SubjectOffering).where(
        SubjectOffering.school_id == school_id,
        SubjectOffering.subject_id == subject_id,
        SubjectOffering.academic_session_id == session_id,
        SubjectOffering.academic_level_id == level_id,
        SubjectOffering.deleted_at.is_(None),
    )
    if exclude_offering_id is not None:
        stmt = stmt.where(SubjectOffering.id != exclude_offering_id)
    existing = (await db.execute(stmt)).scalars().all()
    if department_id is None and any(o.department_id is not None for o in existing):
        raise ValidationException(
            "This subject already has department-specific offerings for this level. "
            "Please retire them before creating a General offering."
        )
    if department_id is not None and any(o.department_id is None for o in existing):
        raise ValidationException(
            "This subject is already offered as General (all departments) for this level, "
            "so separate department offerings are not needed."
        )


async def create_offering(
    db: AsyncSession,
    school_id: UUID,
    req: OfferingCreateRequest,
    current_user: User,
    audit: AuditContext,
) -> SubjectOffering:
    subject = await _get_subject(db, school_id, req.subject_id)

    session_stmt = select(AcademicSession).where(
        AcademicSession.id == req.academic_session_id,
        AcademicSession.school_id == school_id,
        AcademicSession.deleted_at.is_(None),
    )
    session = (await db.execute(session_stmt)).scalar_one_or_none()
    if not session:
        raise ResourceNotFoundException("academic session", req.academic_session_id)

    level_stmt = select(AcademicLevel).where(
        AcademicLevel.id == req.academic_level_id,
        AcademicLevel.school_id == school_id,
        AcademicLevel.deleted_at.is_(None),
    )
    level = (await db.execute(level_stmt)).scalar_one_or_none()
    if not level:
        raise ResourceNotFoundException("academic level", req.academic_level_id)

    department = await _validate_offering_department(db, school_id, level, req.department_id)
    await _validate_offering_scope_exclusivity(
        db,
        school_id,
        subject.id,
        session.id,
        level.id,
        department.id if department else None,
    )
    await require_scoped_permission(
        db,
        current_user,
        "subjects:manage",
        department_id=department.id if department else None,
        academic_level_id=level.id,
    )

    offering = SubjectOffering(
        school_id=school_id,
        subject_id=subject.id,
        academic_session_id=session.id,
        academic_level_id=level.id,
        department_id=department.id if department else None,
    )
    db.add(offering)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SUBJECT_OFFERING_CREATED",
        resource_type="SubjectOffering",
        resource_id=offering.id,
        diff_payload={
            "after": {
                "subject_id": str(subject.id),
                "academic_session_id": str(session.id),
                "academic_level_id": str(level.id),
                "department_id": str(department.id) if department else None,
            }
        },
    )
    await _commit_or_conflict(
        db,
        message=(
            "This subject offering already exists for the selected session, level, and "
            "department. Please edit the existing offering instead."
        ),
        code="OFFERING_CONFLICT",
    )
    await db.refresh(offering)
    return offering


async def get_offering(
    db: AsyncSession, school_id: UUID, offering_id: UUID, current_user: User
) -> SubjectOffering:
    offering = await _get_offering(db, school_id, offering_id)
    await require_scoped_permission(
        db,
        current_user,
        "subjects:manage",
        department_id=offering.department_id,
        academic_level_id=offering.academic_level_id,
    )
    return offering


async def update_offering(
    db: AsyncSession,
    school_id: UUID,
    offering_id: UUID,
    req: OfferingUpdateRequest,
    current_user: User,
    audit: AuditContext,
) -> SubjectOffering:
    offering = await _get_offering(db, school_id, offering_id)
    await require_scoped_permission(
        db,
        current_user,
        "subjects:manage",
        department_id=offering.department_id,
        academic_level_id=offering.academic_level_id,
    )

    active_links = await db.execute(
        select(func.count()).where(
            ClassSubject.subject_offering_id == offering.id,
            ClassSubject.deleted_at.is_(None),
        )
    )
    if active_links.scalar_one() > 0:
        raise ValidationException(
            "This offering is already linked to one or more class groups, so its department "
            "cannot be changed. Please retire the offering and create a new one instead."
        )

    level_stmt = select(AcademicLevel).where(AcademicLevel.id == offering.academic_level_id)
    level = (await db.execute(level_stmt)).scalar_one()

    department = await _validate_offering_department(db, school_id, level, req.department_id)
    await _validate_offering_scope_exclusivity(
        db,
        school_id,
        offering.subject_id,
        offering.academic_session_id,
        offering.academic_level_id,
        department.id if department else None,
        exclude_offering_id=offering.id,
    )

    changes = req.model_dump(exclude_unset=True)
    offering.department_id = department.id if department else None

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SUBJECT_OFFERING_UPDATED",
        resource_type="SubjectOffering",
        resource_id=offering.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This offering could not be saved because it conflicts with another offering.",
        code="OFFERING_CONFLICT",
    )
    await db.refresh(offering)
    return offering


async def retire_offering(
    db: AsyncSession,
    school_id: UUID,
    offering_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> SubjectOffering:
    offering = await _get_offering(db, school_id, offering_id)
    await require_scoped_permission(
        db,
        current_user,
        "subjects:manage",
        department_id=offering.department_id,
        academic_level_id=offering.academic_level_id,
    )
    offering.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SUBJECT_OFFERING_RETIRED",
        resource_type="SubjectOffering",
        resource_id=offering.id,
        diff_payload={"deleted_at": offering.deleted_at},
    )
    await _commit_or_conflict(
        db,
        message="This offering could not be retired. Please try again.",
        code="OFFERING_RETIRE_CONFLICT",
    )
    await db.refresh(offering)
    return offering


# --------------------------------------------------------------------------
# Class Subjects
# --------------------------------------------------------------------------


async def _class_subject_active_usage_count(db: AsyncSession, class_subject_id: UUID) -> int:
    assignments = await db.execute(
        select(func.count()).where(
            TeacherAssignment.class_subject_id == class_subject_id,
            TeacherAssignment.is_active.is_(True),
        )
    )
    registrations = await db.execute(
        select(func.count()).where(
            StudentSubjectRegistration.class_subject_id == class_subject_id,
            StudentSubjectRegistration.is_active.is_(True),
        )
    )
    return assignments.scalar_one() + registrations.scalar_one()


async def list_class_subjects(
    db: AsyncSession,
    school_id: UUID,
    current_user: User,
    *,
    class_group_id: UUID | None,
    subject_offering_id: UUID | None,
    page: int,
    page_size: int,
    include_retired: bool,
    academic_session_id: UUID | None = None,
    academic_level_id: UUID | None = None,
    department_id: UUID | None = None,
    category: str | None = None,
    has_teacher: bool | None = None,
) -> tuple[list[ClassSubject], PageMeta]:
    scope = await get_permission_scope(db, current_user, "subjects:manage")
    if not scope.has_any_access:
        raise InsufficientPermissionException(permission_code="subjects:manage")

    stmt = (
        select(ClassSubject)
        .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
        .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
        .join(Subject, Subject.id == SubjectOffering.subject_id)
        .where(ClassSubject.school_id == school_id)
    )
    if not include_retired:
        stmt = stmt.where(
            ClassSubject.deleted_at.is_(None),
            ClassGroup.deleted_at.is_(None),
            SubjectOffering.deleted_at.is_(None),
            Subject.deleted_at.is_(None),
        )
    if class_group_id is not None:
        stmt = stmt.where(ClassSubject.class_group_id == class_group_id)
    if subject_offering_id is not None:
        stmt = stmt.where(ClassSubject.subject_offering_id == subject_offering_id)
    if academic_session_id is not None:
        stmt = stmt.where(ClassGroup.academic_session_id == academic_session_id)
    if academic_level_id is not None:
        stmt = stmt.where(ClassGroup.academic_level_id == academic_level_id)
    if department_id is not None:
        stmt = stmt.where(ClassGroup.department_id == department_id)
    if category is not None:
        cat = category.strip().upper()
        if cat == "TRADE":
            stmt = stmt.where(Subject.is_trade_subject.is_(True))
        elif cat == "ELECTIVE":
            stmt = stmt.where(Subject.is_elective.is_(True), Subject.is_trade_subject.is_(False))
        elif cat == "CORE":
            stmt = stmt.where(Subject.is_elective.is_(False), Subject.is_trade_subject.is_(False))
        else:
            raise ValidationException(
                "Please choose a valid subject category: CORE, ELECTIVE, or TRADE."
            )
    if has_teacher is True:
        assigned_sub = select(TeacherAssignment.class_subject_id).where(
            TeacherAssignment.school_id == school_id,
            TeacherAssignment.is_active.is_(True),
            TeacherAssignment.deleted_at.is_(None),
        )
        stmt = stmt.where(ClassSubject.id.in_(assigned_sub))
    elif has_teacher is False:
        assigned_sub = select(TeacherAssignment.class_subject_id).where(
            TeacherAssignment.school_id == school_id,
            TeacherAssignment.is_active.is_(True),
            TeacherAssignment.deleted_at.is_(None),
        )
        stmt = stmt.where(ClassSubject.id.not_in(assigned_sub))
    if not scope.unscoped:
        stmt = stmt.where(
            build_scope_filter(
                scope,
                department_column=ClassGroup.department_id,
                academic_level_column=ClassGroup.academic_level_id,
                class_group_column=ClassGroup.id,
            )
        )

    stmt = stmt.order_by(ClassGroup.display_name.asc(), Subject.name.asc())
    items, meta = await paginate(db, stmt, page=page, page_size=page_size)
    if items:
        cs_ids = [cs.id for cs in items]
        meta_rows = (
            await db.execute(
                select(
                    ClassSubject.id,
                    Subject.id,
                    Subject.name,
                    Subject.code,
                    Subject.is_elective,
                    Subject.is_trade_subject,
                    ClassGroup.display_name,
                )
                .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
                .join(Subject, Subject.id == SubjectOffering.subject_id)
                .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
                .where(ClassSubject.id.in_(cs_ids))
            )
        ).all()
        meta_map = {
            cs_id: (
                subj_id,
                s_name,
                s_code,
                "TRADE" if is_trade else ("ELECTIVE" if is_elective else "CORE"),
                cg_name,
            )
            for cs_id, subj_id, s_name, s_code, is_elective, is_trade, cg_name in meta_rows
        }

        ta_rows = (
            await db.execute(
                select(TeacherAssignment.class_subject_id, User.first_name, User.last_name)
                .join(User, User.id == TeacherAssignment.teacher_id)
                .where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.class_subject_id.in_(cs_ids),
                    TeacherAssignment.is_active.is_(True),
                    TeacherAssignment.deleted_at.is_(None),
                )
            )
        ).all()
        ta_map: dict[UUID, list[str]] = {}
        for cs_id, fname, lname in ta_rows:
            ta_map.setdefault(cs_id, []).append(f"{fname} {lname}".strip())

        for cs in items:
            info = meta_map.get(cs.id)
            if info:
                (
                    cs.subject_id,
                    cs.subject_name,
                    cs.subject_code,
                    cs.category,
                    cs.class_group_name,
                ) = info
            cs.teacher_names = ta_map.get(cs.id, [])
    return items, meta


async def get_subjects_summary(
    db: AsyncSession, school_id: UUID, academic_session_id: UUID | None = None
) -> dict[str, int]:
    total_subjects, elective_or_trade = (
        await db.execute(
            select(
                func.count(Subject.id),
                func.count(Subject.id).filter(
                    or_(Subject.is_elective.is_(True), Subject.is_trade_subject.is_(True))
                ),
            ).where(
                Subject.school_id == school_id,
                Subject.deleted_at.is_(None),
            )
        )
    ).one()

    target_session_id = academic_session_id
    if target_session_id is None:
        target_session_id = (
            await db.execute(
                select(AcademicSession.id).where(
                    AcademicSession.school_id == school_id,
                    AcademicSession.is_current.is_(True),
                    AcademicSession.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    class_allocations = 0
    without_teacher = 0
    if target_session_id is not None:
        has_active_teacher = (
            select(TeacherAssignment.id)
            .where(
                TeacherAssignment.school_id == school_id,
                TeacherAssignment.class_subject_id == ClassSubject.id,
                TeacherAssignment.is_active.is_(True),
                TeacherAssignment.deleted_at.is_(None),
            )
            .exists()
        )
        class_allocations, without_teacher = (
            await db.execute(
                select(
                    func.count(ClassSubject.id),
                    func.count(ClassSubject.id).filter(~has_active_teacher),
                )
                .select_from(ClassSubject)
                .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
                .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
                .join(Subject, Subject.id == SubjectOffering.subject_id)
                .where(
                    ClassSubject.school_id == school_id,
                    ClassSubject.deleted_at.is_(None),
                    ClassGroup.deleted_at.is_(None),
                    SubjectOffering.deleted_at.is_(None),
                    Subject.deleted_at.is_(None),
                    ClassGroup.academic_session_id == target_session_id,
                )
            )
        ).one()

    return {
        "total_subjects": total_subjects,
        "class_allocations": class_allocations,
        "without_teacher": without_teacher,
        "elective_or_trade": elective_or_trade,
    }


async def _create_class_subject_record(
    db: AsyncSession,
    school_id: UUID,
    offering: SubjectOffering,
    class_group: ClassGroup,
) -> ClassSubject:
    class_subject = ClassSubject(
        school_id=school_id,
        subject_offering_id=offering.id,
        class_group_id=class_group.id,
    )
    db.add(class_subject)
    await db.flush()
    await seed_assessment_definitions_for_class_subject(db, school_id, class_subject)
    return class_subject


async def get_or_create_class_subject_with_status(
    db: AsyncSession,
    school_id: UUID,
    offering: SubjectOffering,
    class_group: ClassGroup,
) -> tuple[ClassSubject, bool]:
    stmt = select(ClassSubject).where(
        ClassSubject.subject_offering_id == offering.id,
        ClassSubject.class_group_id == class_group.id,
        ClassSubject.deleted_at.is_(None),
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing, False
    return await _create_class_subject_record(db, school_id, offering, class_group), True


async def get_or_create_class_subject(
    db: AsyncSession,
    school_id: UUID,
    offering: SubjectOffering,
    class_group: ClassGroup,
) -> ClassSubject:
    class_subject, _ = await get_or_create_class_subject_with_status(
        db, school_id, offering, class_group
    )
    return class_subject


async def create_class_subject(
    db: AsyncSession,
    school_id: UUID,
    req: ClassSubjectCreateRequest,
    current_user: User,
    audit: AuditContext,
) -> ClassSubject:
    offering = await _get_offering(db, school_id, req.subject_offering_id)
    class_group = await _get_active_class_group(db, school_id, req.class_group_id)
    if class_group.academic_session_id != offering.academic_session_id:
        raise ValidationException(
            "This class group does not belong to the same academic session as the subject "
            "offering. Please choose a class group from the matching session."
        )
    await require_scoped_permission(
        db,
        current_user,
        "subjects:manage",
        department_id=class_group.department_id,
        academic_level_id=class_group.academic_level_id,
        class_group_id=class_group.id,
    )

    existing_stmt = select(ClassSubject.id).where(
        ClassSubject.subject_offering_id == offering.id,
        ClassSubject.class_group_id == class_group.id,
        ClassSubject.deleted_at.is_(None),
    )
    if (await db.execute(existing_stmt)).scalar_one_or_none():
        raise ConflictException(
            message=(
                "This subject is already linked to this class group. Please edit the existing "
                "link instead."
            ),
            code="CLASS_SUBJECT_CONFLICT",
        )

    class_subject = await _create_class_subject_record(db, school_id, offering, class_group)
    record_audit_log(
        db,
        audit,
        action="CLASS_SUBJECT_CREATED",
        resource_type="ClassSubject",
        resource_id=class_subject.id,
        diff_payload={
            "after": {
                "subject_offering_id": str(offering.id),
                "class_group_id": str(class_group.id),
            }
        },
    )
    await _commit_or_conflict(
        db,
        message="This subject is already linked to this class group.",
        code="CLASS_SUBJECT_CONFLICT",
    )
    await db.refresh(class_subject)
    return class_subject


async def get_class_subject(
    db: AsyncSession, school_id: UUID, class_subject_id: UUID, current_user: User
) -> ClassSubject:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject, "subjects:manage")
    return class_subject


async def retire_class_subject(
    db: AsyncSession,
    school_id: UUID,
    class_subject_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> ClassSubject:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject, "subjects:manage")
    usage_count = await _class_subject_active_usage_count(db, class_subject.id)
    if usage_count > 0:
        raise ValidationException(
            "This class subject still has active teacher assignments or student registrations, "
            "so it cannot be retired. Please remove those first."
        )

    class_subject.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="CLASS_SUBJECT_RETIRED",
        resource_type="ClassSubject",
        resource_id=class_subject.id,
        diff_payload={"deleted_at": class_subject.deleted_at},
    )
    await _commit_or_conflict(
        db,
        message="This class subject could not be retired. Please try again.",
        code="CLASS_SUBJECT_RETIRE_CONFLICT",
    )
    await db.refresh(class_subject)
    return class_subject


# --------------------------------------------------------------------------
# Teacher Assignments
# --------------------------------------------------------------------------


async def list_teacher_assignments(
    db: AsyncSession,
    school_id: UUID,
    class_subject_id: UUID,
    current_user: User,
    *,
    page: int,
    page_size: int,
    include_inactive: bool,
) -> tuple[list[TeacherAssignment], PageMeta]:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject, "teachers:assign")
    stmt = select(TeacherAssignment).where(
        TeacherAssignment.school_id == school_id,
        TeacherAssignment.class_subject_id == class_subject_id,
    )
    if not include_inactive:
        stmt = stmt.where(TeacherAssignment.is_active.is_(True))
    stmt = stmt.order_by(TeacherAssignment.start_date.desc())
    return await paginate(db, stmt, page=page, page_size=page_size)


async def _assign_teacher_to_class_subject(
    db: AsyncSession,
    school_id: UUID,
    class_subject: ClassSubject,
    teacher: User,
    assigned_by_id: UUID,
) -> tuple[TeacherAssignment, bool]:
    """Returns (assignment, already_active). Reuses an inactive row if one exists."""
    active_stmt = select(TeacherAssignment).where(
        TeacherAssignment.class_subject_id == class_subject.id,
        TeacherAssignment.teacher_id == teacher.id,
        TeacherAssignment.is_active.is_(True),
    )
    active = (await db.execute(active_stmt)).scalar_one_or_none()
    if active:
        return active, True

    inactive_stmt = (
        select(TeacherAssignment)
        .where(
            TeacherAssignment.class_subject_id == class_subject.id,
            TeacherAssignment.teacher_id == teacher.id,
            TeacherAssignment.is_active.is_(False),
        )
        .order_by(TeacherAssignment.created_at.desc())
        .limit(1)
    )
    inactive = (await db.execute(inactive_stmt)).scalar_one_or_none()
    if inactive:
        inactive.is_active = True
        inactive.end_date = None
        inactive.start_date = date.today()
        inactive.assigned_by_id = assigned_by_id
        await db.flush()
        return inactive, False

    assignment = TeacherAssignment(
        school_id=school_id,
        teacher_id=teacher.id,
        class_subject_id=class_subject.id,
        assigned_by_id=assigned_by_id,
    )
    db.add(assignment)
    await db.flush()
    return assignment, False


async def create_teacher_assignment(
    db: AsyncSession,
    school_id: UUID,
    class_subject_id: UUID,
    teacher_id: UUID,
    assigned_by_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> TeacherAssignment:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject, "teachers:assign")
    teacher = await _validate_teacher(db, school_id, teacher_id)

    assignment, already_active = await _assign_teacher_to_class_subject(
        db, school_id, class_subject, teacher, assigned_by_id
    )
    if already_active:
        raise ConflictException(
            message="This teacher is already assigned to this class subject.",
            code="TEACHER_ASSIGNMENT_CONFLICT",
        )

    record_audit_log(
        db,
        audit,
        action="TEACHER_ASSIGNED",
        resource_type="TeacherAssignment",
        resource_id=assignment.id,
        diff_payload={
            "after": {"teacher_id": str(teacher.id), "class_subject_id": str(class_subject.id)}
        },
    )
    await _commit_or_conflict(
        db,
        message="This teacher could not be assigned to this class subject.",
        code="TEACHER_ASSIGNMENT_CONFLICT",
    )
    await db.refresh(assignment)
    return assignment


async def deactivate_teacher_assignment(
    db: AsyncSession,
    school_id: UUID,
    class_subject_id: UUID,
    assignment_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> TeacherAssignment:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject, "teachers:assign")

    stmt = select(TeacherAssignment).where(
        TeacherAssignment.id == assignment_id,
        TeacherAssignment.class_subject_id == class_subject_id,
        TeacherAssignment.school_id == school_id,
        TeacherAssignment.is_active.is_(True),
    )
    assignment = (await db.execute(stmt)).scalar_one_or_none()
    if not assignment:
        raise ResourceNotFoundException("teacher assignment", assignment_id)

    assignment.is_active = False
    assignment.end_date = date.today()
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="TEACHER_UNASSIGNED",
        resource_type="TeacherAssignment",
        resource_id=assignment.id,
        diff_payload={"after": {"is_active": False, "end_date": str(assignment.end_date)}},
    )
    await _commit_or_conflict(
        db,
        message="This teacher assignment could not be removed. Please try again.",
        code="TEACHER_ASSIGNMENT_CONFLICT",
    )
    await db.refresh(assignment)
    return assignment


async def bulk_assign_teacher(
    db: AsyncSession,
    school_id: UUID,
    req: TeacherAssignmentBulkCreateRequest,
    assigned_by_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> tuple[list[TeacherAssignment], list[TeacherAssignmentBulkSkip]]:
    offering = await _get_offering(db, school_id, req.subject_offering_id)
    teacher = await _validate_teacher(db, school_id, req.teacher_id)

    assigned: list[TeacherAssignment] = []
    skipped: list[TeacherAssignmentBulkSkip] = []

    for class_group_id in req.class_group_ids:
        try:
            class_group = await _get_active_class_group(db, school_id, class_group_id)
        except ResourceNotFoundException:
            skipped.append(
                TeacherAssignmentBulkSkip(
                    class_group_id=class_group_id,
                    reason="This class group could not be found in your school.",
                )
            )
            continue
        if class_group.academic_session_id != offering.academic_session_id:
            skipped.append(
                TeacherAssignmentBulkSkip(
                    class_group_id=class_group_id,
                    reason="This class group does not belong to the same academic session as the subject offering.",
                )
            )
            continue
        try:
            await require_scoped_permission(
                db,
                current_user,
                "teachers:assign",
                department_id=class_group.department_id,
                academic_level_id=class_group.academic_level_id,
                class_group_id=class_group.id,
            )
        except InsufficientPermissionException:
            skipped.append(
                TeacherAssignmentBulkSkip(
                    class_group_id=class_group_id,
                    reason="You don't have permission to assign teachers for this class group.",
                )
            )
            continue

        class_subject = await get_or_create_class_subject(db, school_id, offering, class_group)
        assignment, already_active = await _assign_teacher_to_class_subject(
            db, school_id, class_subject, teacher, assigned_by_id
        )
        if already_active:
            skipped.append(
                TeacherAssignmentBulkSkip(
                    class_group_id=class_group_id,
                    reason="This teacher is already assigned to this class subject.",
                )
            )
            continue
        assigned.append(assignment)

    record_audit_log(
        db,
        audit,
        action="TEACHER_BULK_ASSIGNED",
        resource_type="TeacherAssignment",
        resource_id=teacher.id,
        diff_payload={
            "after": {
                "teacher_id": str(teacher.id),
                "subject_offering_id": str(offering.id),
                "assigned_class_group_ids": [str(a.class_subject_id) for a in assigned],
            }
        },
    )
    await _commit_or_conflict(
        db,
        message="This bulk teacher assignment could not be saved. Please try again.",
        code="TEACHER_ASSIGNMENT_CONFLICT",
    )
    for assignment in assigned:
        await db.refresh(assignment)
    return assigned, skipped


# --------------------------------------------------------------------------
# Student Subject Registrations
# --------------------------------------------------------------------------


async def list_subject_registrations(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    current_user: User,
    *,
    page: int,
    page_size: int,
    include_inactive: bool,
) -> tuple[list[StudentSubjectRegistration], PageMeta]:
    enrollment = await _get_enrollment(db, school_id, enrollment_id)
    await _require_enrollment_scope(db, current_user, enrollment, "subjects:register_electives")
    stmt = select(StudentSubjectRegistration).where(
        StudentSubjectRegistration.school_id == school_id,
        StudentSubjectRegistration.student_enrollment_id == enrollment_id,
    )
    if not include_inactive:
        stmt = stmt.where(StudentSubjectRegistration.is_active.is_(True))
    return await paginate(db, stmt, page=page, page_size=page_size)


async def _get_enrollment(
    db: AsyncSession, school_id: UUID, enrollment_id: UUID
) -> StudentEnrollment:
    stmt = select(StudentEnrollment).where(
        StudentEnrollment.id == enrollment_id,
        StudentEnrollment.school_id == school_id,
        StudentEnrollment.deleted_at.is_(None),
    )
    enrollment = (await db.execute(stmt)).scalar_one_or_none()
    if not enrollment:
        raise ResourceNotFoundException("student enrollment", enrollment_id)
    return enrollment


async def _register_subject_for_enrollment(
    db: AsyncSession,
    school_id: UUID,
    enrollment: StudentEnrollment,
    class_subject: ClassSubject,
    subject: Subject,
) -> StudentSubjectRegistration:
    if not subject.is_elective:
        raise ValidationException(
            "This subject is automatically registered when a student enrolls and cannot be "
            "registered manually."
        )
    category = (
        AUTO_REGISTER_TRADE_SUBJECT if subject.is_trade_subject else AUTO_REGISTER_FREE_ELECTIVE
    )

    existing_stmt = select(StudentSubjectRegistration).where(
        StudentSubjectRegistration.student_enrollment_id == enrollment.id,
        StudentSubjectRegistration.class_subject_id == class_subject.id,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing:
        if existing.is_active:
            raise ConflictException(
                message="This student is already registered for this subject.",
                code="SUBJECT_REGISTRATION_CONFLICT",
            )
        existing.is_active = True
        existing.subject_category = category
        await db.flush()
        return existing

    registration = StudentSubjectRegistration(
        school_id=school_id,
        student_enrollment_id=enrollment.id,
        class_subject_id=class_subject.id,
        subject_category=category,
    )
    db.add(registration)
    await db.flush()
    return registration


async def create_subject_registration(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    class_subject_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> StudentSubjectRegistration:
    enrollment = await _get_enrollment(db, school_id, enrollment_id)
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    if class_subject.class_group_id != enrollment.class_group_id:
        raise ValidationException(
            "This subject is not offered to the student's current class group."
        )
    await _require_enrollment_scope(db, current_user, enrollment, "subjects:register_electives")

    offering_stmt = select(SubjectOffering).where(
        SubjectOffering.id == class_subject.subject_offering_id
    )
    offering = (await db.execute(offering_stmt)).scalar_one()
    subject_stmt = select(Subject).where(Subject.id == offering.subject_id)
    subject = (await db.execute(subject_stmt)).scalar_one()

    registration = await _register_subject_for_enrollment(
        db, school_id, enrollment, class_subject, subject
    )
    record_audit_log(
        db,
        audit,
        action="STUDENT_SUBJECT_REGISTERED",
        resource_type="StudentSubjectRegistration",
        resource_id=registration.id,
        diff_payload={
            "after": {
                "student_enrollment_id": str(enrollment.id),
                "class_subject_id": str(class_subject.id),
                "subject_category": registration.subject_category,
            }
        },
    )
    await _commit_or_conflict(
        db,
        message="This student could not be registered for this subject.",
        code="SUBJECT_REGISTRATION_CONFLICT",
    )
    await db.refresh(registration)
    return registration


async def deactivate_subject_registration(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    registration_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> StudentSubjectRegistration:
    enrollment = await _get_enrollment(db, school_id, enrollment_id)
    await _require_enrollment_scope(db, current_user, enrollment, "subjects:register_electives")

    stmt = select(StudentSubjectRegistration).where(
        StudentSubjectRegistration.id == registration_id,
        StudentSubjectRegistration.student_enrollment_id == enrollment_id,
        StudentSubjectRegistration.school_id == school_id,
        StudentSubjectRegistration.is_active.is_(True),
    )
    registration = (await db.execute(stmt)).scalar_one_or_none()
    if not registration:
        raise ResourceNotFoundException("subject registration", registration_id)

    registration.is_active = False
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="STUDENT_SUBJECT_UNREGISTERED",
        resource_type="StudentSubjectRegistration",
        resource_id=registration.id,
        diff_payload={"after": {"is_active": False}},
    )
    await _commit_or_conflict(
        db,
        message="This subject registration could not be removed. Please try again.",
        code="SUBJECT_REGISTRATION_CONFLICT",
    )
    await db.refresh(registration)
    return registration


async def bulk_register_subject(
    db: AsyncSession,
    school_id: UUID,
    class_subject_id: UUID,
    enrollment_ids: list[UUID],
    current_user: User,
    audit: AuditContext,
) -> tuple[list[StudentSubjectRegistration], list[tuple[UUID, str]]]:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    await _require_class_subject_scope(
        db, current_user, class_subject, "subjects:register_electives"
    )
    offering_stmt = select(SubjectOffering).where(
        SubjectOffering.id == class_subject.subject_offering_id
    )
    offering = (await db.execute(offering_stmt)).scalar_one()
    subject_stmt = select(Subject).where(Subject.id == offering.subject_id)
    subject = (await db.execute(subject_stmt)).scalar_one()

    registered: list[StudentSubjectRegistration] = []
    skipped: list[tuple[UUID, str]] = []

    for enrollment_id in enrollment_ids:
        try:
            enrollment = await _get_enrollment(db, school_id, enrollment_id)
        except ResourceNotFoundException:
            skipped.append((enrollment_id, "This student enrollment could not be found."))
            continue
        if enrollment.class_group_id != class_subject.class_group_id:
            skipped.append(
                (enrollment_id, "This subject is not offered to the student's class group.")
            )
            continue
        try:
            registration = await _register_subject_for_enrollment(
                db, school_id, enrollment, class_subject, subject
            )
        except (ValidationException, ConflictException) as exc:
            skipped.append((enrollment_id, exc.message))
            continue
        registered.append(registration)

    record_audit_log(
        db,
        audit,
        action="STUDENT_SUBJECT_BULK_REGISTERED",
        resource_type="ClassSubject",
        resource_id=class_subject.id,
        diff_payload={"registered_count": len(registered), "skipped_count": len(skipped)},
    )
    await _commit_or_conflict(
        db,
        message="This bulk subject registration could not be saved. Please try again.",
        code="SUBJECT_REGISTRATION_CONFLICT",
    )
    for registration in registered:
        await db.refresh(registration)
    return registered, skipped


# --------------------------------------------------------------------------
# Enrollment auto-registration hook (called from students.services)
# --------------------------------------------------------------------------


async def auto_register_compulsory_subjects(
    db: AsyncSession,
    school_id: UUID,
    enrollment: StudentEnrollment,
    class_group: ClassGroup,
    audit: AuditContext,
) -> list[StudentSubjectRegistration]:
    """Registers every non-elective class subject offered to a class group when a
    student is enrolled into it. Electives (including trade subjects) are never
    auto-registered; they require an explicit registration call.
    """
    stmt = (
        select(ClassSubject, SubjectOffering, Subject)
        .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
        .join(Subject, Subject.id == SubjectOffering.subject_id)
        .where(
            ClassSubject.class_group_id == class_group.id,
            ClassSubject.deleted_at.is_(None),
            SubjectOffering.deleted_at.is_(None),
            Subject.deleted_at.is_(None),
            Subject.is_elective.is_(False),
        )
    )
    rows = (await db.execute(stmt)).all()

    registrations: list[StudentSubjectRegistration] = []
    for class_subject, offering, _subject in rows:
        category = "COMPULSORY_CORE" if offering.department_id is None else "DEPARTMENT_CORE"
        registration = StudentSubjectRegistration(
            school_id=school_id,
            student_enrollment_id=enrollment.id,
            class_subject_id=class_subject.id,
            subject_category=category,
        )
        db.add(registration)
        registrations.append(registration)

    if registrations:
        await db.flush()
        record_audit_log(
            db,
            audit,
            action="STUDENT_AUTO_REGISTERED_SUBJECTS",
            resource_type="StudentEnrollment",
            resource_id=enrollment.id,
            diff_payload={
                "class_subject_ids": [str(r.class_subject_id) for r in registrations],
            },
        )
    return registrations


async def backfill_compulsory_registrations_for_class_group(
    db: AsyncSession,
    school_id: UUID,
    class_group: ClassGroup,
) -> int:
    """Registers compulsory core subjects for students already enrolled in a class.

    This is used when a structure/subject template adds subjects to an existing
    class group, so historical enrollments receive the same roster as new ones.
    """
    subject_rows = (
        await db.execute(
            select(ClassSubject, SubjectOffering)
            .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
            .join(Subject, Subject.id == SubjectOffering.subject_id)
            .where(
                ClassSubject.school_id == school_id,
                ClassSubject.class_group_id == class_group.id,
                ClassSubject.deleted_at.is_(None),
                SubjectOffering.deleted_at.is_(None),
                Subject.deleted_at.is_(None),
                Subject.is_elective.is_(False),
            )
        )
    ).all()
    if not subject_rows:
        return 0

    enrollments = (
        (
            await db.execute(
                select(StudentEnrollment).where(
                    StudentEnrollment.school_id == school_id,
                    StudentEnrollment.class_group_id == class_group.id,
                    StudentEnrollment.enrollment_status.in_(["ENROLLED", "REPEATING"]),
                    StudentEnrollment.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not enrollments:
        return 0

    enrollment_ids = [enrollment.id for enrollment in enrollments]
    class_subject_ids = [class_subject.id for class_subject, _ in subject_rows]
    existing_pairs = set(
        (
            await db.execute(
                select(
                    StudentSubjectRegistration.student_enrollment_id,
                    StudentSubjectRegistration.class_subject_id,
                ).where(
                    StudentSubjectRegistration.school_id == school_id,
                    StudentSubjectRegistration.student_enrollment_id.in_(enrollment_ids),
                    StudentSubjectRegistration.class_subject_id.in_(class_subject_ids),
                    StudentSubjectRegistration.is_active.is_(True),
                )
            )
        ).all()
    )

    registrations: list[StudentSubjectRegistration] = []
    for enrollment in enrollments:
        for class_subject, offering in subject_rows:
            key = (enrollment.id, class_subject.id)
            if key in existing_pairs:
                continue
            category = "COMPULSORY_CORE" if offering.department_id is None else "DEPARTMENT_CORE"
            registration = StudentSubjectRegistration(
                school_id=school_id,
                student_enrollment_id=enrollment.id,
                class_subject_id=class_subject.id,
                subject_category=category,
            )
            db.add(registration)
            existing_pairs.add(key)
            registrations.append(registration)

    if registrations:
        await db.flush()
    return len(registrations)


# --------------------------------------------------------------------------
# Roster filtering (consumed by the assessments module)
# --------------------------------------------------------------------------


async def get_registered_enrollment_ids_for_class_subject(
    db: AsyncSession,
    school_id: UUID,
    class_subject_id: UUID,
) -> list[UUID]:
    stmt = select(StudentSubjectRegistration.student_enrollment_id).where(
        StudentSubjectRegistration.school_id == school_id,
        StudentSubjectRegistration.class_subject_id == class_subject_id,
        StudentSubjectRegistration.is_active.is_(True),
    )
    return list((await db.execute(stmt)).scalars().all())


async def is_teacher_assigned_to_class_subject(
    db: AsyncSession,
    school_id: UUID,
    teacher_id: UUID,
    class_subject_id: UUID,
) -> bool:
    stmt = select(TeacherAssignment.id).where(
        TeacherAssignment.school_id == school_id,
        TeacherAssignment.teacher_id == teacher_id,
        TeacherAssignment.class_subject_id == class_subject_id,
        TeacherAssignment.is_active.is_(True),
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def get_class_subject_with_context(
    db: AsyncSession,
    school_id: UUID,
    class_subject_id: UUID,
) -> tuple[ClassSubject, SubjectOffering, Subject]:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    offering_stmt = select(SubjectOffering).where(
        SubjectOffering.id == class_subject.subject_offering_id
    )
    offering = (await db.execute(offering_stmt)).scalar_one()
    subject_stmt = select(Subject).where(Subject.id == offering.subject_id)
    subject = (await db.execute(subject_stmt)).scalar_one()
    return class_subject, offering, subject


async def list_my_teaching_assignments(
    db: AsyncSession,
    school_id: UUID,
    current_user: User,
    *,
    academic_session_id: UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[MyTeachingAssignmentResponse], PageMeta]:
    base_stmt = (
        select(TeacherAssignment, ClassSubject, ClassGroup, Subject)
        .join(ClassSubject, ClassSubject.id == TeacherAssignment.class_subject_id)
        .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
        .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
        .join(Subject, Subject.id == SubjectOffering.subject_id)
        .where(
            TeacherAssignment.school_id == school_id,
            TeacherAssignment.teacher_id == current_user.id,
            TeacherAssignment.is_active.is_(True),
            TeacherAssignment.deleted_at.is_(None),
            ClassSubject.deleted_at.is_(None),
            ClassGroup.deleted_at.is_(None),
            SubjectOffering.deleted_at.is_(None),
            Subject.deleted_at.is_(None),
        )
    )
    if academic_session_id is not None:
        base_stmt = base_stmt.where(ClassGroup.academic_session_id == academic_session_id)

    total_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one() or 0)
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 0

    rows = (
        await db.execute(
            base_stmt.order_by(ClassGroup.display_name.asc(), Subject.name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = [
        MyTeachingAssignmentResponse(
            assignment_id=assignment.id,
            class_subject_id=class_subject.id,
            class_group_id=class_group.id,
            class_group_name=class_group.display_name,
            academic_session_id=class_group.academic_session_id,
            subject_id=subject.id,
            subject_name=subject.name,
            subject_code=subject.code,
            is_elective=subject.is_elective,
            is_trade_subject=subject.is_trade_subject,
            start_date=assignment.start_date,
        )
        for assignment, class_subject, class_group, subject in rows
    ]
    return items, PageMeta(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


async def list_class_group_subjects(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    current_user: User,
) -> list[ClassGroupSubjectItem]:
    class_group = await _get_active_class_group(db, school_id, class_group_id)

    if class_group.class_teacher_id != current_user.id:
        allowed = False
        for perm_code in ("subjects:manage", "teachers:assign", "structure:manage"):
            try:
                await require_scoped_permission(
                    db,
                    current_user,
                    perm_code,
                    department_id=class_group.department_id,
                    academic_level_id=class_group.academic_level_id,
                    class_group_id=class_group.id,
                )
                allowed = True
                break
            except InsufficientPermissionException:
                continue
        if not allowed:
            raise InsufficientPermissionException("subjects:manage")

    cs_rows = (
        await db.execute(
            select(ClassSubject, SubjectOffering, Subject)
            .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
            .join(Subject, Subject.id == SubjectOffering.subject_id)
            .where(
                ClassSubject.school_id == school_id,
                ClassSubject.class_group_id == class_group.id,
                ClassSubject.deleted_at.is_(None),
                SubjectOffering.deleted_at.is_(None),
                Subject.deleted_at.is_(None),
            )
            .order_by(Subject.name.asc())
        )
    ).all()

    cs_ids = [cs.id for cs, _, _ in cs_rows]
    teachers_by_cs: dict[UUID, list[ClassSubjectTeacherSummary]] = {}
    if cs_ids:
        ta_rows = (
            await db.execute(
                select(TeacherAssignment, User)
                .join(User, User.id == TeacherAssignment.teacher_id)
                .where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.class_subject_id.in_(cs_ids),
                    TeacherAssignment.is_active.is_(True),
                    TeacherAssignment.deleted_at.is_(None),
                )
                .order_by(User.first_name.asc(), User.last_name.asc())
            )
        ).all()
        for assignment, teacher in ta_rows:
            teachers_by_cs.setdefault(assignment.class_subject_id, []).append(
                ClassSubjectTeacherSummary(
                    assignment_id=assignment.id,
                    teacher_id=teacher.id,
                    first_name=teacher.first_name,
                    last_name=teacher.last_name,
                    email=teacher.email,
                    start_date=assignment.start_date,
                )
            )

    return [
        ClassGroupSubjectItem(
            class_subject_id=cs.id,
            subject_offering_id=offering.id,
            subject_id=subject.id,
            subject_name=subject.name,
            subject_code=subject.code,
            is_elective=subject.is_elective,
            is_trade_subject=subject.is_trade_subject,
            teachers=teachers_by_cs.get(cs.id, []),
        )
        for cs, offering, subject in cs_rows
    ]


# --------------------------------------------------------------------------
# Teacher Subject Qualifications ("this teacher takes this subject", level/
# class-independent -- distinct from TeacherAssignment). Set once per teacher
# from the Staff page; used to soft-filter (prioritize, never hide) the
# teacher pool offered when assigning a subject teacher to a class arm.
# --------------------------------------------------------------------------


async def get_teacher_subject_qualifications(
    db: AsyncSession,
    school_id: UUID,
    teacher_id: UUID,
) -> TeacherSubjectQualificationsResponse:
    teacher = await _validate_teacher(db, school_id, teacher_id)
    level_categories_row = (
        await db.execute(
            select(StaffProfile.level_categories).where(
                StaffProfile.school_id == school_id,
                StaffProfile.user_id == teacher.id,
            )
        )
    ).scalar_one_or_none()
    level_categories = list(level_categories_row or [])
    subjects = (
        (
            await db.execute(
                select(Subject)
                .join(
                    TeacherSubjectQualification,
                    TeacherSubjectQualification.subject_id == Subject.id,
                )
                .where(
                    TeacherSubjectQualification.school_id == school_id,
                    TeacherSubjectQualification.teacher_id == teacher.id,
                    TeacherSubjectQualification.deleted_at.is_(None),
                    Subject.deleted_at.is_(None),
                )
                .order_by(Subject.name.asc())
            )
        )
        .scalars()
        .all()
    )
    return TeacherSubjectQualificationsResponse(
        teacher_id=teacher.id,
        level_categories=level_categories,
        subjects=list(subjects),
    )


async def set_teacher_subject_qualifications(
    db: AsyncSession,
    school_id: UUID,
    teacher_id: UUID,
    subject_ids: list[UUID],
    audit: AuditContext,
    *,
    level_categories: list[str] | None = None,
) -> TeacherSubjectQualificationsResponse:
    teacher = await _validate_teacher(db, school_id, teacher_id)

    requested_ids = set(subject_ids)
    if requested_ids:
        valid_count = (
            await db.execute(
                select(func.count()).where(
                    Subject.id.in_(requested_ids),
                    Subject.school_id == school_id,
                    Subject.deleted_at.is_(None),
                )
            )
        ).scalar_one()
        if valid_count != len(requested_ids):
            raise ValidationException(
                "One or more selected subjects could not be found in your school's catalog."
            )

    existing = (
        (
            await db.execute(
                select(TeacherSubjectQualification).where(
                    TeacherSubjectQualification.school_id == school_id,
                    TeacherSubjectQualification.teacher_id == teacher.id,
                    TeacherSubjectQualification.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_subject = {q.subject_id: q for q in existing}

    for subject_id, qualification in existing_by_subject.items():
        if subject_id not in requested_ids:
            qualification.deleted_at = datetime.now(UTC)

    newly_added = requested_ids - set(existing_by_subject.keys())
    for subject_id in newly_added:
        db.add(
            TeacherSubjectQualification(
                school_id=school_id,
                teacher_id=teacher.id,
                subject_id=subject_id,
            )
        )

    normalized_categories: list[str] | None = None
    if level_categories is not None:
        normalized_categories = list(dict.fromkeys(level_categories))
        sp = (
            await db.execute(
                select(StaffProfile).where(
                    StaffProfile.school_id == school_id,
                    StaffProfile.user_id == teacher.id,
                )
            )
        ).scalar_one_or_none()
        if sp is not None:
            sp.level_categories = normalized_categories
        else:
            db.add(
                StaffProfile(
                    school_id=school_id,
                    user_id=teacher.id,
                    staff_id_number=f"STAFF-{str(teacher.id)[:8].upper()}",
                    level_categories=normalized_categories,
                )
            )

    diff_after: dict[str, object] = {"subject_ids": [str(sid) for sid in sorted(requested_ids)]}
    if normalized_categories is not None:
        diff_after["level_categories"] = normalized_categories

    record_audit_log(
        db,
        audit,
        action="TEACHER_SUBJECT_QUALIFICATIONS_UPDATED",
        resource_type="TeacherSubjectQualification",
        resource_id=teacher.id,
        diff_payload={"after": diff_after},
    )
    await _commit_or_conflict(
        db,
        message="These subject qualifications could not be saved. Please try again.",
        code="TEACHER_SUBJECT_QUALIFICATION_CONFLICT",
    )
    return await get_teacher_subject_qualifications(db, school_id, teacher.id)


async def get_qualified_teachers_for_subject(
    db: AsyncSession,
    school_id: UUID,
    subject_id: UUID,
    current_user: User,
) -> list[QualifiedTeacherResponse]:
    scope = await get_permission_scope(db, current_user, "subjects:manage")
    if not scope.has_any_access:
        raise InsufficientPermissionException(permission_code="subjects:manage")
    await _get_subject(db, school_id, subject_id)

    rows = (
        await db.execute(
            select(User, StaffProfile.level_categories)
            .join(
                TeacherSubjectQualification,
                TeacherSubjectQualification.teacher_id == User.id,
            )
            .outerjoin(StaffProfile, StaffProfile.user_id == User.id)
            .where(
                TeacherSubjectQualification.school_id == school_id,
                TeacherSubjectQualification.subject_id == subject_id,
                TeacherSubjectQualification.deleted_at.is_(None),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(User.first_name.asc(), User.last_name.asc())
        )
    ).all()
    return [
        QualifiedTeacherResponse(
            teacher_id=u.id,
            first_name=u.first_name,
            last_name=u.last_name,
            email=u.email,
            level_categories=list(level_cats or []),
        )
        for u, level_cats in rows
    ]
