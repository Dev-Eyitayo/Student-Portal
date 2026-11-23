from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select, update
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
    AcademicTerm,
    ClassArm,
    ClassGroup,
    Department,
)
from app.modules.academic_structure.schemas import (
    ArmCreateRequest,
    ArmUpdateRequest,
    ClassGroupCreateRequest,
    ClassGroupUpdateRequest,
    DepartmentCreateRequest,
    DepartmentUpdateRequest,
    LevelCreateRequest,
    LevelUpdateRequest,
    SessionCreateRequest,
    SessionRolloverRequest,
    SessionRolloverResponse,
    SessionUpdateRequest,
    StructureTemplateApplyRequest,
    StructureTemplateApplyResponse,
    StructureTemplateType,
    TermCreateRequest,
    TermUpdateRequest,
)
from app.modules.academic_structure.templates import (
    COMBINED_STRUCTURE_TEMPLATE_ID,
    PRIMARY_STRUCTURE_TEMPLATE_ID,
    SECONDARY_STRUCTURE_TEMPLATE_ID,
    get_structure_template_preview,
)
from app.modules.assessments.services import (
    seed_assessment_definitions_for_class_subject,
    seed_assessment_definitions_for_term,
)
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.common.pagination import paginate
from app.modules.common.schemas import PageMeta
from app.modules.identity.models import User
from app.modules.students.models import StudentEnrollment
from app.modules.subjects.models import ClassSubject, Subject, SubjectOffering, TeacherAssignment
from app.modules.subjects.services import (
    backfill_compulsory_registrations_for_class_group,
    get_or_create_class_subject_with_status,
)

CLASS_TEACHER_ROLES = {"TEACHER", "ACADEMIC_OFFICER", "SCHOOL_ADMIN"}


async def _commit_or_conflict(
    db: AsyncSession,
    *,
    message: str,
    code: str = "CONFLICT",
) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictException(message=message, code=code) from exc


async def _unset_current_sessions(
    db: AsyncSession,
    school_id: UUID,
    *,
    exclude_id: UUID | None = None,
) -> None:
    stmt = (
        update(AcademicSession)
        .where(
            AcademicSession.school_id == school_id,
            AcademicSession.deleted_at.is_(None),
            AcademicSession.is_current.is_(True),
        )
        .values(is_current=False, updated_at=datetime.now(UTC))
    )
    if exclude_id is not None:
        stmt = stmt.where(AcademicSession.id != exclude_id)
    await db.execute(stmt)


async def _unset_current_terms(
    db: AsyncSession,
    school_id: UUID,
    *,
    exclude_id: UUID | None = None,
) -> None:
    stmt = (
        update(AcademicTerm)
        .where(
            AcademicTerm.school_id == school_id,
            AcademicTerm.deleted_at.is_(None),
            AcademicTerm.is_current.is_(True),
        )
        .values(is_current=False, updated_at=datetime.now(UTC))
    )
    if exclude_id is not None:
        stmt = stmt.where(AcademicTerm.id != exclude_id)
    await db.execute(stmt)


async def _ensure_session_name_available(
    db: AsyncSession,
    school_id: UUID,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    stmt = select(AcademicSession.id).where(
        AcademicSession.school_id == school_id,
        AcademicSession.deleted_at.is_(None),
        AcademicSession.name == name,
    )
    if exclude_id is not None:
        stmt = stmt.where(AcademicSession.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise ConflictException(
            message=(
                "An academic session with this name already exists. Please choose a different "
                "name or edit the existing session."
            ),
            code="SESSION_NAME_TAKEN",
        )


async def _ensure_term_fields_available(
    db: AsyncSession,
    session_id: UUID,
    *,
    name: str | None = None,
    term_order: int | None = None,
    exclude_id: UUID | None = None,
) -> None:
    if name is not None:
        stmt = select(AcademicTerm.id).where(
            AcademicTerm.academic_session_id == session_id,
            AcademicTerm.deleted_at.is_(None),
            AcademicTerm.name == name,
        )
        if exclude_id is not None:
            stmt = stmt.where(AcademicTerm.id != exclude_id)
        if (await db.execute(stmt)).scalar_one_or_none():
            raise ConflictException(
                message=(
                    "A term with this name already exists in this session. Please choose a "
                    "different name or edit the existing term."
                ),
                code="TERM_NAME_TAKEN",
            )

    if term_order is not None:
        stmt = select(AcademicTerm.id).where(
            AcademicTerm.academic_session_id == session_id,
            AcademicTerm.deleted_at.is_(None),
            AcademicTerm.term_order == term_order,
        )
        if exclude_id is not None:
            stmt = stmt.where(AcademicTerm.id != exclude_id)
        if (await db.execute(stmt)).scalar_one_or_none():
            raise ConflictException(
                message=(
                    "Another term already uses this term position in this session. Please "
                    "choose a different position or edit the existing term."
                ),
                code="TERM_ORDER_TAKEN",
            )


async def _ensure_level_fields_available(
    db: AsyncSession,
    school_id: UUID,
    *,
    code: str | None = None,
    level_order: int | None = None,
    exclude_id: UUID | None = None,
) -> None:
    if code is not None:
        stmt = select(AcademicLevel.id).where(
            AcademicLevel.school_id == school_id,
            AcademicLevel.deleted_at.is_(None),
            AcademicLevel.code == code,
        )
        if exclude_id is not None:
            stmt = stmt.where(AcademicLevel.id != exclude_id)
        if (await db.execute(stmt)).scalar_one_or_none():
            raise ConflictException(
                message=(
                    "A class level with this code already exists. Please choose a different "
                    "code or edit the existing level."
                ),
                code="LEVEL_CODE_TAKEN",
            )

    if level_order is not None:
        stmt = select(AcademicLevel.id).where(
            AcademicLevel.school_id == school_id,
            AcademicLevel.deleted_at.is_(None),
            AcademicLevel.level_order == level_order,
        )
        if exclude_id is not None:
            stmt = stmt.where(AcademicLevel.id != exclude_id)
        if (await db.execute(stmt)).scalar_one_or_none():
            raise ConflictException(
                message=(
                    "Another class level already uses this position. Please choose a different "
                    "position or edit the existing level."
                ),
                code="LEVEL_ORDER_TAKEN",
            )


async def _ensure_department_code_available(
    db: AsyncSession,
    school_id: UUID,
    code: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    stmt = select(Department.id).where(
        Department.school_id == school_id,
        Department.deleted_at.is_(None),
        Department.code == code,
    )
    if exclude_id is not None:
        stmt = stmt.where(Department.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise ConflictException(
            message=(
                "A department with this code already exists. Please choose a different code "
                "or edit the existing department."
            ),
            code="DEPARTMENT_CODE_TAKEN",
        )


async def _ensure_arm_name_available(
    db: AsyncSession,
    school_id: UUID,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    stmt = select(ClassArm.id).where(
        ClassArm.school_id == school_id,
        ClassArm.deleted_at.is_(None),
        ClassArm.name == name,
    )
    if exclude_id is not None:
        stmt = stmt.where(ClassArm.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise ConflictException(
            message=(
                "A class arm with this name already exists. Please choose a different name "
                "or edit the existing arm."
            ),
            code="ARM_NAME_TAKEN",
        )


async def _get_session(
    db: AsyncSession,
    school_id: UUID,
    session_id: UUID,
    *,
    include_retired: bool = False,
) -> AcademicSession:
    stmt = select(AcademicSession).where(
        AcademicSession.id == session_id,
        AcademicSession.school_id == school_id,
    )
    if not include_retired:
        stmt = stmt.where(AcademicSession.deleted_at.is_(None))
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise ResourceNotFoundException("academic session", session_id)
    return session


async def _get_term(
    db: AsyncSession,
    school_id: UUID,
    term_id: UUID,
    *,
    include_retired: bool = False,
) -> AcademicTerm:
    stmt = select(AcademicTerm).where(
        AcademicTerm.id == term_id,
        AcademicTerm.school_id == school_id,
    )
    if not include_retired:
        stmt = stmt.where(AcademicTerm.deleted_at.is_(None))
    term = (await db.execute(stmt)).scalar_one_or_none()
    if not term:
        raise ResourceNotFoundException("academic term", term_id)
    return term


async def _get_level(
    db: AsyncSession,
    school_id: UUID,
    level_id: UUID,
    *,
    include_retired: bool = False,
) -> AcademicLevel:
    stmt = select(AcademicLevel).where(
        AcademicLevel.id == level_id,
        AcademicLevel.school_id == school_id,
    )
    if not include_retired:
        stmt = stmt.where(AcademicLevel.deleted_at.is_(None))
    level = (await db.execute(stmt)).scalar_one_or_none()
    if not level:
        raise ResourceNotFoundException("academic level", level_id)
    return level


async def _get_department(
    db: AsyncSession,
    school_id: UUID,
    department_id: UUID,
    *,
    include_retired: bool = False,
) -> Department:
    stmt = select(Department).where(
        Department.id == department_id,
        Department.school_id == school_id,
    )
    if not include_retired:
        stmt = stmt.where(Department.deleted_at.is_(None))
    department = (await db.execute(stmt)).scalar_one_or_none()
    if not department:
        raise ResourceNotFoundException("department", department_id)
    return department


async def _get_arm(
    db: AsyncSession,
    school_id: UUID,
    arm_id: UUID,
    *,
    include_retired: bool = False,
) -> ClassArm:
    stmt = select(ClassArm).where(
        ClassArm.id == arm_id,
        ClassArm.school_id == school_id,
    )
    if not include_retired:
        stmt = stmt.where(ClassArm.deleted_at.is_(None))
    arm = (await db.execute(stmt)).scalar_one_or_none()
    if not arm:
        raise ResourceNotFoundException("class arm", arm_id)
    return arm


async def _get_class_group(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    *,
    include_retired: bool = False,
) -> ClassGroup:
    stmt = select(ClassGroup).where(
        ClassGroup.id == class_group_id,
        ClassGroup.school_id == school_id,
    )
    if not include_retired:
        stmt = stmt.where(ClassGroup.deleted_at.is_(None))
    class_group = (await db.execute(stmt)).scalar_one_or_none()
    if not class_group:
        raise ResourceNotFoundException("class group", class_group_id)
    return class_group


async def _validate_class_teacher(
    db: AsyncSession,
    school_id: UUID,
    teacher_id: UUID,
    *,
    session_id: UUID | None = None,
    exclude_class_group_id: UUID | None = None,
) -> User:
    stmt = select(User).where(
        User.id == teacher_id,
        User.school_id == school_id,
        User.is_active.is_(True),
    )
    teacher = (await db.execute(stmt)).scalar_one_or_none()
    if not teacher:
        raise ValidationException(
            "We couldn't find an active staff member with this ID in your school. Please choose "
            "a different class teacher."
        )
    if teacher.role not in CLASS_TEACHER_ROLES:
        raise ValidationException(
            "This staff member cannot be assigned as a class teacher. Please choose a teacher, "
            "academic officer, or school administrator."
        )
    if session_id is not None:
        conflict_stmt = select(ClassGroup.display_name).where(
            ClassGroup.school_id == school_id,
            ClassGroup.academic_session_id == session_id,
            ClassGroup.class_teacher_id == teacher_id,
            ClassGroup.deleted_at.is_(None),
        )
        if exclude_class_group_id is not None:
            conflict_stmt = conflict_stmt.where(ClassGroup.id != exclude_class_group_id)
        existing_arm_name = (await db.execute(conflict_stmt.limit(1))).scalar_one_or_none()
        if existing_arm_name is not None:
            teacher_name = f"{teacher.first_name} {teacher.last_name}".strip()
            raise ConflictException(
                message=(
                    f"{teacher_name} is already assigned as the class teacher for "
                    f"{existing_arm_name} in this academic session. A teacher can only be "
                    "assigned to one class arm per session."
                ),
                code="CLASS_TEACHER_SESSION_CONFLICT",
            )
    return teacher


def _validate_term_dates(
    session: AcademicSession,
    start_date: date,
    end_date: date,
) -> None:
    if end_date <= start_date:
        raise ValidationException("The term end date must be after its start date.")
    if start_date < session.start_date or end_date > session.end_date:
        raise ValidationException(
            "The term dates must fall within the academic session dates. Please adjust the "
            "term dates and try again."
        )


async def _validate_session_term_coverage(
    db: AsyncSession,
    session: AcademicSession,
    start_date: date,
    end_date: date,
) -> None:
    if end_date <= start_date:
        raise ValidationException("The session end date must be after its start date.")
    stmt = select(AcademicTerm).where(
        AcademicTerm.academic_session_id == session.id,
        AcademicTerm.deleted_at.is_(None),
    )
    terms = list((await db.execute(stmt)).scalars().all())
    for term in terms:
        if term.start_date < start_date or term.end_date > end_date:
            raise ValidationException(
                "The new session dates must still cover all of its term dates. Please adjust "
                "the term dates first, then update the session."
            )


def _build_class_group_name(
    level: AcademicLevel,
    department: Department | None,
    arm: ClassArm,
) -> str:
    if department is not None:
        return f"{level.code} {department.name} {arm.name}"
    return f"{level.code} {arm.name}"


async def _ensure_class_group_available(
    db: AsyncSession,
    school_id: UUID,
    *,
    session_id: UUID,
    level_id: UUID,
    department_id: UUID | None,
    arm_id: UUID,
    exclude_id: UUID | None = None,
) -> None:
    stmt = select(ClassGroup.id).where(
        ClassGroup.school_id == school_id,
        ClassGroup.deleted_at.is_(None),
        ClassGroup.academic_session_id == session_id,
        ClassGroup.academic_level_id == level_id,
        ClassGroup.class_arm_id == arm_id,
    )
    if department_id is None:
        stmt = stmt.where(ClassGroup.department_id.is_(None))
    else:
        stmt = stmt.where(ClassGroup.department_id == department_id)
    if exclude_id is not None:
        stmt = stmt.where(ClassGroup.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise ConflictException(
            message=(
                "This class group already exists for the selected session, level, department, "
                "and arm. Please edit the existing class group instead."
            ),
            code="CLASS_GROUP_TAKEN",
        )


async def _class_group_enrollment_count(
    db: AsyncSession,
    class_group_id: UUID,
) -> int:
    stmt = select(func.count()).where(
        StudentEnrollment.class_group_id == class_group_id,
        StudentEnrollment.deleted_at.is_(None),
        StudentEnrollment.enrollment_status == "ENROLLED",
    )
    return (await db.execute(stmt)).scalar_one()


async def list_sessions(
    db: AsyncSession,
    school_id: UUID,
    *,
    page: int,
    page_size: int,
    include_retired: bool,
) -> tuple[list[AcademicSession], PageMeta]:
    stmt = select(AcademicSession).where(AcademicSession.school_id == school_id)
    if not include_retired:
        stmt = stmt.where(AcademicSession.deleted_at.is_(None))
    stmt = stmt.order_by(AcademicSession.start_date.desc(), AcademicSession.name.asc())
    return await paginate(db, stmt, page=page, page_size=page_size)


async def create_session(
    db: AsyncSession,
    school_id: UUID,
    req: SessionCreateRequest,
    audit: AuditContext,
) -> AcademicSession:
    name = req.name.strip()
    await _ensure_session_name_available(db, school_id, name)
    if req.is_current:
        await _unset_current_sessions(db, school_id)

    session = AcademicSession(
        school_id=school_id,
        name=name,
        start_date=req.start_date,
        end_date=req.end_date,
        is_current=req.is_current,
    )
    db.add(session)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ACADEMIC_SESSION_CREATED",
        resource_type="AcademicSession",
        resource_id=session.id,
        diff_payload={"after": {"name": name, "is_current": req.is_current}},
    )
    await _commit_or_conflict(
        db,
        message="An academic session with this name already exists. Please choose another name.",
        code="SESSION_NAME_TAKEN",
    )
    await db.refresh(session)
    return session


async def get_session(db: AsyncSession, school_id: UUID, session_id: UUID) -> AcademicSession:
    return await _get_session(db, school_id, session_id)


async def update_session(
    db: AsyncSession,
    school_id: UUID,
    session_id: UUID,
    req: SessionUpdateRequest,
    audit: AuditContext,
) -> AcademicSession:
    session = await _get_session(db, school_id, session_id)
    changes = req.model_dump(exclude_unset=True)

    if "name" in changes and req.name is not None:
        name = req.name.strip()
        await _ensure_session_name_available(db, school_id, name, exclude_id=session.id)
        session.name = name

    new_start = req.start_date if req.start_date is not None else session.start_date
    new_end = req.end_date if req.end_date is not None else session.end_date
    if req.start_date is not None or req.end_date is not None:
        await _validate_session_term_coverage(db, session, new_start, new_end)
        session.start_date = new_start
        session.end_date = new_end

    if req.is_current is True:
        await _unset_current_sessions(db, school_id, exclude_id=session.id)
        session.is_current = True
    elif req.is_current is False:
        session.is_current = False

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ACADEMIC_SESSION_UPDATED",
        resource_type="AcademicSession",
        resource_id=session.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This academic session could not be saved because its details conflict with another session.",
        code="SESSION_CONFLICT",
    )
    await db.refresh(session)
    return session


async def retire_session(
    db: AsyncSession,
    school_id: UUID,
    session_id: UUID,
    audit: AuditContext,
) -> AcademicSession:
    session = await _get_session(db, school_id, session_id)
    if session.is_current:
        raise ValidationException(
            "This is the current academic session, so it cannot be retired yet. Please mark "
            "another session as current first."
        )

    active_enrollments = await db.execute(
        select(StudentEnrollment.id).where(
            StudentEnrollment.academic_session_id == session.id,
            StudentEnrollment.deleted_at.is_(None),
            StudentEnrollment.enrollment_status == "ENROLLED",
        )
    )
    if active_enrollments.scalars().first():
        raise ValidationException(
            "This session still has enrolled students, so it cannot be retired. Please move "
            "those students to another session first."
        )

    session.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ACADEMIC_SESSION_RETIRED",
        resource_type="AcademicSession",
        resource_id=session.id,
        diff_payload={"deleted_at": session.deleted_at},
    )
    await _commit_or_conflict(
        db,
        message="This academic session could not be retired. Please try again.",
        code="SESSION_RETIRE_CONFLICT",
    )
    await db.refresh(session)
    return session


async def _attach_term_status(db: AsyncSession, school_id: UUID, terms: list[AcademicTerm]) -> None:
    if not terms:
        return
    from app.modules.computation.models import TermReportCard

    term_ids = [t.id for t in terms]
    published_ids = set(
        (
            await db.execute(
                select(TermReportCard.academic_term_id)
                .where(
                    TermReportCard.school_id == school_id,
                    TermReportCard.academic_term_id.in_(term_ids),
                    TermReportCard.publication_status == "PUBLISHED",
                    TermReportCard.deleted_at.is_(None),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    for t in terms:
        if t.id in published_ids:
            t.status = "PUBLISHED"
        elif t.is_locked_for_scoring:
            t.status = "LOCKED"
        else:
            t.status = "DRAFT"


async def list_terms(
    db: AsyncSession,
    school_id: UUID,
    session_id: UUID,
    *,
    page: int,
    page_size: int,
    include_retired: bool,
) -> tuple[list[AcademicTerm], PageMeta]:
    await _get_session(db, school_id, session_id, include_retired=True)
    stmt = select(AcademicTerm).where(
        AcademicTerm.school_id == school_id,
        AcademicTerm.academic_session_id == session_id,
    )
    if not include_retired:
        stmt = stmt.where(AcademicTerm.deleted_at.is_(None))
    stmt = stmt.order_by(AcademicTerm.term_order.asc())
    items, meta = await paginate(db, stmt, page=page, page_size=page_size)
    await _attach_term_status(db, school_id, items)
    return items, meta


async def create_term(
    db: AsyncSession,
    school_id: UUID,
    session_id: UUID,
    req: TermCreateRequest,
    audit: AuditContext,
) -> AcademicTerm:
    session = await _get_session(db, school_id, session_id)
    name = req.name.strip()
    _validate_term_dates(session, req.start_date, req.end_date)
    await _ensure_term_fields_available(
        db,
        session.id,
        name=name,
        term_order=req.term_order,
    )
    if req.is_current:
        await _unset_current_terms(db, school_id)

    term = AcademicTerm(
        school_id=school_id,
        academic_session_id=session.id,
        name=name,
        term_order=req.term_order,
        start_date=req.start_date,
        end_date=req.end_date,
        is_current=req.is_current,
        is_locked_for_scoring=req.is_locked_for_scoring,
    )
    db.add(term)
    await db.flush()
    await seed_assessment_definitions_for_term(db, school_id, term)
    record_audit_log(
        db,
        audit,
        action="ACADEMIC_TERM_CREATED",
        resource_type="AcademicTerm",
        resource_id=term.id,
        diff_payload={
            "after": {
                "name": name,
                "term_order": req.term_order,
                "is_current": req.is_current,
            }
        },
    )
    await _commit_or_conflict(
        db,
        message="A term with this name or position already exists in this session.",
        code="TERM_CONFLICT",
    )
    await db.refresh(term)
    return term


async def get_term(db: AsyncSession, school_id: UUID, term_id: UUID) -> AcademicTerm:
    term = await _get_term(db, school_id, term_id)
    await _attach_term_status(db, school_id, [term])
    return term


async def update_term(
    db: AsyncSession,
    school_id: UUID,
    term_id: UUID,
    req: TermUpdateRequest,
    audit: AuditContext,
) -> AcademicTerm:
    term = await _get_term(db, school_id, term_id)
    changes = req.model_dump(exclude_unset=True)
    if term.is_locked_for_scoring:
        if changes != {"is_locked_for_scoring": False}:
            raise ValidationException(
                "This term is locked for scoring, so its details cannot be changed. Submit only "
                "`is_locked_for_scoring: false` by itself to unlock it before making other changes."
            )
        term.is_locked_for_scoring = False
        await db.flush()
        record_audit_log(
            db,
            audit,
            action="ACADEMIC_TERM_UPDATED",
            resource_type="AcademicTerm",
            resource_id=term.id,
            diff_payload={"changes": changes},
        )
        await _commit_or_conflict(
            db,
            message="This term could not be saved because its details conflict with another term.",
            code="TERM_CONFLICT",
        )
        await db.refresh(term)
        return term

    session = await _get_session(db, school_id, term.academic_session_id, include_retired=True)

    new_start = req.start_date if req.start_date is not None else term.start_date
    new_end = req.end_date if req.end_date is not None else term.end_date
    _validate_term_dates(session, new_start, new_end)

    name = req.name.strip() if req.name is not None else term.name
    new_order = req.term_order if req.term_order is not None else term.term_order
    await _ensure_term_fields_available(
        db,
        session.id,
        name=name if req.name is not None else None,
        term_order=new_order if req.term_order is not None else None,
        exclude_id=term.id,
    )

    term.start_date = new_start
    term.end_date = new_end
    if req.name is not None:
        term.name = name
    if req.term_order is not None:
        term.term_order = new_order
    if req.is_locked_for_scoring is not None:
        term.is_locked_for_scoring = req.is_locked_for_scoring

    if req.is_current is True:
        await _unset_current_terms(db, school_id, exclude_id=term.id)
        term.is_current = True
    elif req.is_current is False:
        term.is_current = False

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ACADEMIC_TERM_UPDATED",
        resource_type="AcademicTerm",
        resource_id=term.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This term could not be saved because its details conflict with another term.",
        code="TERM_CONFLICT",
    )
    await db.refresh(term)
    return term


async def retire_term(
    db: AsyncSession,
    school_id: UUID,
    term_id: UUID,
    audit: AuditContext,
) -> AcademicTerm:
    term = await _get_term(db, school_id, term_id)
    if term.is_locked_for_scoring:
        raise ValidationException(
            "This term is locked for scoring, so it cannot be retired. Please unlock it first."
        )
    if term.is_current:
        raise ValidationException(
            "This is the current term, so it cannot be retired yet. Please mark another term "
            "as current first."
        )

    term.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ACADEMIC_TERM_RETIRED",
        resource_type="AcademicTerm",
        resource_id=term.id,
        diff_payload={"deleted_at": term.deleted_at},
    )
    await _commit_or_conflict(
        db,
        message="This term could not be retired. Please try again.",
        code="TERM_RETIRE_CONFLICT",
    )
    await db.refresh(term)
    return term


async def list_levels(
    db: AsyncSession,
    school_id: UUID,
    *,
    page: int,
    page_size: int,
    include_retired: bool,
) -> tuple[list[AcademicLevel], PageMeta]:
    stmt = select(AcademicLevel).where(AcademicLevel.school_id == school_id)
    if not include_retired:
        stmt = stmt.where(AcademicLevel.deleted_at.is_(None))
    stmt = stmt.order_by(AcademicLevel.level_order.asc())
    return await paginate(db, stmt, page=page, page_size=page_size)


async def create_level(
    db: AsyncSession,
    school_id: UUID,
    req: LevelCreateRequest,
    audit: AuditContext,
) -> AcademicLevel:
    code = req.code.strip().upper()
    await _ensure_level_fields_available(
        db,
        school_id,
        code=code,
        level_order=req.level_order,
    )
    level = AcademicLevel(
        school_id=school_id,
        name=req.name.strip(),
        code=code,
        level_order=req.level_order,
        category=req.category.value,
    )
    db.add(level)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ACADEMIC_LEVEL_CREATED",
        resource_type="AcademicLevel",
        resource_id=level.id,
        diff_payload={"after": {"name": level.name, "code": code, "ordering": level.level_order}},
    )
    await _commit_or_conflict(
        db,
        message="A class level with this code or position already exists. Please adjust the details.",
        code="LEVEL_CONFLICT",
    )
    await db.refresh(level)
    return level


async def get_level(db: AsyncSession, school_id: UUID, level_id: UUID) -> AcademicLevel:
    return await _get_level(db, school_id, level_id)


async def update_level(
    db: AsyncSession,
    school_id: UUID,
    level_id: UUID,
    req: LevelUpdateRequest,
    audit: AuditContext,
) -> AcademicLevel:
    level = await _get_level(db, school_id, level_id)
    changes = req.model_dump(exclude_unset=True)
    code = req.code.strip().upper() if req.code is not None else None
    await _ensure_level_fields_available(
        db,
        school_id,
        code=code,
        level_order=req.level_order,
        exclude_id=level.id,
    )

    if req.name is not None:
        level.name = req.name.strip()
    if code is not None:
        level.code = code
    if req.level_order is not None:
        level.level_order = req.level_order
    if req.category is not None:
        if req.category.value in ("PRIMARY", "JUNIOR_SECONDARY"):
            has_dept_group = (
                await db.execute(
                    select(ClassGroup.id).where(
                        ClassGroup.school_id == school_id,
                        ClassGroup.academic_level_id == level.id,
                        ClassGroup.department_id.is_not(None),
                        ClassGroup.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            has_dept_offering = (
                await db.execute(
                    select(SubjectOffering.id).where(
                        SubjectOffering.school_id == school_id,
                        SubjectOffering.academic_level_id == level.id,
                        SubjectOffering.department_id.is_not(None),
                        SubjectOffering.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if has_dept_group is not None or has_dept_offering is not None:
                level_label = "Primary" if req.category.value == "PRIMARY" else "Junior secondary"
                raise ValidationException(
                    f"{level_label} levels cannot use departments, and this level already has "
                    "classes or subject offerings assigned to a department."
                )
        level.category = req.category.value

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ACADEMIC_LEVEL_UPDATED",
        resource_type="AcademicLevel",
        resource_id=level.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This class level could not be saved because its details conflict with another level.",
        code="LEVEL_CONFLICT",
    )
    await db.refresh(level)
    return level


async def retire_level(
    db: AsyncSession,
    school_id: UUID,
    level_id: UUID,
    audit: AuditContext,
) -> AcademicLevel:
    level = await _get_level(db, school_id, level_id)
    level.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ACADEMIC_LEVEL_RETIRED",
        resource_type="AcademicLevel",
        resource_id=level.id,
        diff_payload={"deleted_at": level.deleted_at},
    )
    await _commit_or_conflict(
        db,
        message="This class level could not be retired. Please try again.",
        code="LEVEL_RETIRE_CONFLICT",
    )
    await db.refresh(level)
    return level


async def list_departments(
    db: AsyncSession,
    school_id: UUID,
    *,
    page: int,
    page_size: int,
    include_retired: bool,
) -> tuple[list[Department], PageMeta]:
    stmt = select(Department).where(Department.school_id == school_id)
    if not include_retired:
        stmt = stmt.where(Department.deleted_at.is_(None))
    stmt = stmt.order_by(Department.name.asc())
    return await paginate(db, stmt, page=page, page_size=page_size)


async def create_department(
    db: AsyncSession,
    school_id: UUID,
    req: DepartmentCreateRequest,
    audit: AuditContext,
) -> Department:
    code = req.code.strip().upper()
    await _ensure_department_code_available(db, school_id, code)
    department = Department(
        school_id=school_id,
        name=req.name.strip(),
        code=code,
    )
    db.add(department)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="DEPARTMENT_CREATED",
        resource_type="Department",
        resource_id=department.id,
        diff_payload={"after": {"name": department.name, "code": code}},
    )
    await _commit_or_conflict(
        db,
        message="A department with this code already exists. Please choose another code.",
        code="DEPARTMENT_CONFLICT",
    )
    await db.refresh(department)
    return department


async def get_department(
    db: AsyncSession,
    school_id: UUID,
    department_id: UUID,
) -> Department:
    return await _get_department(db, school_id, department_id)


async def update_department(
    db: AsyncSession,
    school_id: UUID,
    department_id: UUID,
    req: DepartmentUpdateRequest,
    audit: AuditContext,
) -> Department:
    department = await _get_department(db, school_id, department_id)
    changes = req.model_dump(exclude_unset=True)
    code = req.code.strip().upper() if req.code is not None else None
    if code is not None:
        await _ensure_department_code_available(
            db,
            school_id,
            code,
            exclude_id=department.id,
        )
    if req.name is not None:
        department.name = req.name.strip()
    if code is not None:
        department.code = code

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="DEPARTMENT_UPDATED",
        resource_type="Department",
        resource_id=department.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This department could not be saved because its details conflict with another department.",
        code="DEPARTMENT_CONFLICT",
    )
    await db.refresh(department)
    return department


async def retire_department(
    db: AsyncSession,
    school_id: UUID,
    department_id: UUID,
    audit: AuditContext,
) -> Department:
    department = await _get_department(db, school_id, department_id)
    department.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="DEPARTMENT_RETIRED",
        resource_type="Department",
        resource_id=department.id,
        diff_payload={"deleted_at": department.deleted_at},
    )
    await _commit_or_conflict(
        db,
        message="This department could not be retired. Please try again.",
        code="DEPARTMENT_RETIRE_CONFLICT",
    )
    await db.refresh(department)
    return department


async def list_arms(
    db: AsyncSession,
    school_id: UUID,
    *,
    page: int,
    page_size: int,
    include_retired: bool,
) -> tuple[list[ClassArm], PageMeta]:
    stmt = select(ClassArm).where(ClassArm.school_id == school_id)
    if not include_retired:
        stmt = stmt.where(ClassArm.deleted_at.is_(None))
    stmt = stmt.order_by(ClassArm.name.asc())
    return await paginate(db, stmt, page=page, page_size=page_size)


async def create_arm(
    db: AsyncSession,
    school_id: UUID,
    req: ArmCreateRequest,
    audit: AuditContext,
) -> ClassArm:
    name = req.name.strip()
    await _ensure_arm_name_available(db, school_id, name)
    arm = ClassArm(school_id=school_id, name=name)
    db.add(arm)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="CLASS_ARM_CREATED",
        resource_type="ClassArm",
        resource_id=arm.id,
        diff_payload={"after": {"name": name}},
    )
    await _commit_or_conflict(
        db,
        message="A class arm with this name already exists. Please choose another name.",
        code="ARM_CONFLICT",
    )
    await db.refresh(arm)
    return arm


async def get_arm(db: AsyncSession, school_id: UUID, arm_id: UUID) -> ClassArm:
    return await _get_arm(db, school_id, arm_id)


async def update_arm(
    db: AsyncSession,
    school_id: UUID,
    arm_id: UUID,
    req: ArmUpdateRequest,
    audit: AuditContext,
) -> ClassArm:
    arm = await _get_arm(db, school_id, arm_id)
    changes = req.model_dump(exclude_unset=True)
    if req.name is not None:
        name = req.name.strip()
        await _ensure_arm_name_available(db, school_id, name, exclude_id=arm.id)
        arm.name = name

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="CLASS_ARM_UPDATED",
        resource_type="ClassArm",
        resource_id=arm.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This class arm could not be saved because its name conflicts with another arm.",
        code="ARM_CONFLICT",
    )
    await db.refresh(arm)
    return arm


async def retire_arm(
    db: AsyncSession,
    school_id: UUID,
    arm_id: UUID,
    audit: AuditContext,
) -> ClassArm:
    arm = await _get_arm(db, school_id, arm_id)
    arm.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="CLASS_ARM_RETIRED",
        resource_type="ClassArm",
        resource_id=arm.id,
        diff_payload={"deleted_at": arm.deleted_at},
    )
    await _commit_or_conflict(
        db,
        message="This class arm could not be retired. Please try again.",
        code="ARM_RETIRE_CONFLICT",
    )
    await db.refresh(arm)
    return arm


def _class_group_query(
    school_id: UUID,
    *,
    session_id: UUID | None,
    level_id: UUID | None,
    department_id: UUID | None,
    include_retired: bool,
    has_class_teacher: bool | None = None,
) -> Select[tuple[ClassGroup]]:
    stmt = select(ClassGroup).where(ClassGroup.school_id == school_id)
    if not include_retired:
        stmt = stmt.where(ClassGroup.deleted_at.is_(None))
    if session_id is not None:
        stmt = stmt.where(ClassGroup.academic_session_id == session_id)
    if level_id is not None:
        stmt = stmt.where(ClassGroup.academic_level_id == level_id)
    if department_id is not None:
        stmt = stmt.where(ClassGroup.department_id == department_id)
    if has_class_teacher is True:
        stmt = stmt.where(ClassGroup.class_teacher_id.is_not(None))
    elif has_class_teacher is False:
        stmt = stmt.where(ClassGroup.class_teacher_id.is_(None))
    return stmt.order_by(ClassGroup.display_name.asc())


async def _enrich_class_groups(db: AsyncSession, school_id: UUID, items: list[ClassGroup]) -> None:
    if not items:
        return
    from app.modules.students.models import StudentEnrollment

    cg_ids = [cg.id for cg in items]
    teacher_ids = [cg.class_teacher_id for cg in items if cg.class_teacher_id is not None]
    session_ids = list({cg.academic_session_id for cg in items})
    level_ids = list({cg.academic_level_id for cg in items})
    dept_ids = list({cg.department_id for cg in items if cg.department_id is not None})
    arm_ids = list({cg.class_arm_id for cg in items})

    teacher_map: dict[UUID, str] = {}
    if teacher_ids:
        teacher_rows = (
            await db.execute(
                select(User.id, User.first_name, User.last_name).where(User.id.in_(teacher_ids))
            )
        ).all()
        teacher_map = {uid: f"{fname} {lname}".strip() for uid, fname, lname in teacher_rows}

    session_map = dict(
        (
            await db.execute(
                select(AcademicSession.id, AcademicSession.name).where(
                    AcademicSession.id.in_(session_ids)
                )
            )
        ).all()
    )
    level_map = dict(
        (
            await db.execute(
                select(AcademicLevel.id, AcademicLevel.name).where(AcademicLevel.id.in_(level_ids))
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
    arm_map = dict(
        (await db.execute(select(ClassArm.id, ClassArm.name).where(ClassArm.id.in_(arm_ids)))).all()
    )

    count_rows = (
        await db.execute(
            select(StudentEnrollment.class_group_id, func.count())
            .where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.class_group_id.in_(cg_ids),
                StudentEnrollment.enrollment_status == "ENROLLED",
                StudentEnrollment.deleted_at.is_(None),
            )
            .group_by(StudentEnrollment.class_group_id)
        )
    ).all()
    count_map = {cg_id: cnt for cg_id, cnt in count_rows}

    for cg in items:
        cg.class_teacher_name = (
            teacher_map.get(cg.class_teacher_id) if cg.class_teacher_id else None
        )
        cg.student_count = count_map.get(cg.id, 0)
        cg.academic_session_name = session_map.get(cg.academic_session_id)
        cg.academic_level_name = level_map.get(cg.academic_level_id)
        cg.department_name = dept_map.get(cg.department_id) if cg.department_id else None
        cg.class_arm_name = arm_map.get(cg.class_arm_id)


async def list_class_groups(
    db: AsyncSession,
    school_id: UUID,
    current_user: User,
    *,
    session_id: UUID | None,
    level_id: UUID | None,
    department_id: UUID | None,
    page: int,
    page_size: int,
    include_retired: bool,
    has_class_teacher: bool | None = None,
) -> tuple[list[ClassGroup], PageMeta]:

    from app.modules.subjects.models import ClassSubject, TeacherAssignment

    scope = await get_permission_scope(db, current_user, "structure:manage")
    stmt = _class_group_query(
        school_id,
        session_id=session_id,
        level_id=level_id,
        department_id=department_id,
        include_retired=include_retired,
        has_class_teacher=has_class_teacher,
    )
    if scope.has_any_access:
        if not scope.unscoped:
            stmt = stmt.where(
                build_scope_filter(
                    scope,
                    department_column=ClassGroup.department_id,
                    academic_level_column=ClassGroup.academic_level_id,
                    class_group_column=ClassGroup.id,
                )
            )
    else:
        assigned_cg_ids = set(
            (
                await db.execute(
                    select(ClassSubject.class_group_id)
                    .join(TeacherAssignment, TeacherAssignment.class_subject_id == ClassSubject.id)
                    .where(
                        TeacherAssignment.school_id == school_id,
                        TeacherAssignment.teacher_id == current_user.id,
                        TeacherAssignment.is_active.is_(True),
                        TeacherAssignment.deleted_at.is_(None),
                        ClassSubject.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        form_cg_ids = set(
            (
                await db.execute(
                    select(ClassGroup.id).where(
                        ClassGroup.school_id == school_id,
                        ClassGroup.class_teacher_id == current_user.id,
                        ClassGroup.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        allowed_cg_ids = assigned_cg_ids | form_cg_ids
        if not allowed_cg_ids:
            raise InsufficientPermissionException(permission_code="structure:manage")
        stmt = stmt.where(ClassGroup.id.in_(allowed_cg_ids))
    items, meta = await paginate(db, stmt, page=page, page_size=page_size)
    await _enrich_class_groups(db, school_id, items)
    return items, meta


async def create_class_group(
    db: AsyncSession,
    school_id: UUID,
    req: ClassGroupCreateRequest,
    current_user: User,
    audit: AuditContext,
) -> ClassGroup:
    session = await _get_session(db, school_id, req.academic_session_id)
    level = await _get_level(db, school_id, req.academic_level_id)
    arm = await _get_arm(db, school_id, req.class_arm_id)

    department: Department | None = None
    if level.category in ("JUNIOR_SECONDARY", "PRIMARY") and req.department_id is not None:
        level_label = "Primary" if level.category == "PRIMARY" else "Junior secondary"
        raise ValidationException(
            f"{level_label} classes do not use a department. Please leave the department "
            "empty for this class."
        )
    if req.department_id is not None:
        department = await _get_department(db, school_id, req.department_id)

    await require_scoped_permission(
        db,
        current_user,
        "structure:manage",
        department_id=department.id if department else None,
        academic_level_id=level.id,
    )

    if req.class_teacher_id is not None:
        await _validate_class_teacher(
            db,
            school_id,
            req.class_teacher_id,
            session_id=session.id,
        )

    await _ensure_class_group_available(
        db,
        school_id,
        session_id=session.id,
        level_id=level.id,
        department_id=department.id if department else None,
        arm_id=arm.id,
    )

    display_name = (
        req.display_name.strip()
        if req.display_name
        else _build_class_group_name(level, department, arm)
    )
    class_group = ClassGroup(
        school_id=school_id,
        academic_session_id=session.id,
        academic_level_id=level.id,
        department_id=department.id if department else None,
        class_arm_id=arm.id,
        class_teacher_id=req.class_teacher_id,
        display_name=display_name,
    )
    db.add(class_group)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="CLASS_GROUP_CREATED",
        resource_type="ClassGroup",
        resource_id=class_group.id,
        diff_payload={"after": {"display_name": display_name}},
    )
    await _commit_or_conflict(
        db,
        message="This class group already exists. Please edit the existing class group instead.",
        code="CLASS_GROUP_CONFLICT",
    )
    await db.refresh(class_group)
    return class_group


async def _require_class_group_scope(
    db: AsyncSession,
    current_user: User,
    class_group: ClassGroup,
    permission_code: str = "structure:manage",
) -> None:
    await require_scoped_permission(
        db,
        current_user,
        permission_code,
        department_id=class_group.department_id,
        academic_level_id=class_group.academic_level_id,
        class_group_id=class_group.id,
    )


async def get_class_group(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    current_user: User,
) -> ClassGroup:
    class_group = await _get_class_group(db, school_id, class_group_id)
    if class_group.class_teacher_id != current_user.id:
        from app.modules.subjects.models import ClassSubject, TeacherAssignment

        is_subject_teacher = (
            await db.execute(
                select(TeacherAssignment.id)
                .join(ClassSubject, ClassSubject.id == TeacherAssignment.class_subject_id)
                .where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == current_user.id,
                    TeacherAssignment.is_active.is_(True),
                    TeacherAssignment.deleted_at.is_(None),
                    ClassSubject.class_group_id == class_group.id,
                    ClassSubject.deleted_at.is_(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if is_subject_teacher is None:
            await _require_class_group_scope(db, current_user, class_group)
    await _enrich_class_groups(db, school_id, [class_group])
    return class_group


async def update_class_group(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    req: ClassGroupUpdateRequest,
    current_user: User,
    audit: AuditContext,
) -> ClassGroup:
    class_group = await _get_class_group(db, school_id, class_group_id)
    await _require_class_group_scope(db, current_user, class_group)
    changes = req.model_dump(exclude_unset=True)

    if "class_teacher_id" in req.model_fields_set:
        if req.class_teacher_id is not None:
            await _validate_class_teacher(
                db,
                school_id,
                req.class_teacher_id,
                session_id=class_group.academic_session_id,
                exclude_class_group_id=class_group.id,
            )
        class_group.class_teacher_id = req.class_teacher_id
    if req.display_name is not None:
        class_group.display_name = req.display_name.strip()

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="CLASS_GROUP_UPDATED",
        resource_type="ClassGroup",
        resource_id=class_group.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This class group could not be saved. Please try again.",
        code="CLASS_GROUP_CONFLICT",
    )
    await db.refresh(class_group)
    return class_group


async def retire_class_group(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> ClassGroup:
    class_group = await _get_class_group(db, school_id, class_group_id)
    await _require_class_group_scope(db, current_user, class_group)
    enrollment_count = await _class_group_enrollment_count(db, class_group.id)
    if enrollment_count > 0:
        raise ValidationException(
            f"This class group still has {enrollment_count} enrolled student(s), so it cannot "
            "be retired. Please move those students to another class first."
        )

    class_group.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="CLASS_GROUP_RETIRED",
        resource_type="ClassGroup",
        resource_id=class_group.id,
        diff_payload={"deleted_at": class_group.deleted_at},
    )
    await _commit_or_conflict(
        db,
        message="This class group could not be retired. Please try again.",
        code="CLASS_GROUP_RETIRE_CONFLICT",
    )
    await db.refresh(class_group)
    return class_group


def _resolve_structure_template_id(req: StructureTemplateApplyRequest) -> str:
    if req.template_id is not None:
        return req.template_id
    if req.template_type == StructureTemplateType.PRIMARY:
        return PRIMARY_STRUCTURE_TEMPLATE_ID
    if req.template_type == StructureTemplateType.COMBINED:
        return COMBINED_STRUCTURE_TEMPLATE_ID
    return SECONDARY_STRUCTURE_TEMPLATE_ID


async def _get_or_create_core_subject(
    db: AsyncSession,
    school_id: UUID,
    item: dict[str, Any],
) -> tuple[Subject, bool]:
    code = item["code"].strip().upper()
    stmt = select(Subject).where(
        Subject.school_id == school_id,
        Subject.code == code,
        Subject.deleted_at.is_(None),
    )
    subject = (await db.execute(stmt)).scalar_one_or_none()
    if subject is not None:
        return subject, False

    subject = Subject(
        school_id=school_id,
        name=item["name"].strip(),
        code=code,
        is_elective=False,
        is_trade_subject=False,
    )
    db.add(subject)
    await db.flush()
    return subject, True


async def _provision_template_subjects(
    db: AsyncSession,
    school_id: UUID,
    session_id: UUID,
    preview: dict[str, Any],
    levels_by_code: dict[str, AcademicLevel],
    *,
    departments_by_code: dict[str, Department] | None = None,
    selected_subject_codes: list[str] | None = None,
    separate_senior_departments: bool = True,
) -> dict[str, int]:
    subject_items = preview.get("subjects") or []
    counts = {
        "subjects_created": 0,
        "subjects_reused": 0,
        "subject_offerings_created": 0,
        "subject_offerings_reused": 0,
        "class_subjects_created": 0,
        "class_subjects_reused": 0,
        "subject_registrations_created": 0,
    }
    if selected_subject_codes is not None:
        allowed_codes = {code.strip().upper() for code in selected_subject_codes if code.strip()}
        subject_items = [
            item for item in subject_items if str(item["code"]).strip().upper() in allowed_codes
        ]
    if not subject_items:
        return counts

    class_groups = (
        (
            await db.execute(
                select(ClassGroup).where(
                    ClassGroup.school_id == school_id,
                    ClassGroup.academic_session_id == session_id,
                    ClassGroup.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    groups_by_level: dict[UUID, list[ClassGroup]] = {}
    for group in class_groups:
        groups_by_level.setdefault(group.academic_level_id, []).append(group)

    existing_subjects = (
        (
            await db.execute(
                select(Subject).where(
                    Subject.school_id == school_id,
                    Subject.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    subjects_by_code = {subj.code.upper(): subj for subj in existing_subjects}
    subjects_by_name = {subj.name.lower(): subj for subj in existing_subjects}

    new_subjects: list[Subject] = []
    resolved_items: list[tuple[Subject, dict[str, Any]]] = []
    for item in subject_items:
        code_upper = str(item["code"]).strip().upper()
        name_lower = str(item["name"]).strip().lower()
        existing = subjects_by_code.get(code_upper) or subjects_by_name.get(name_lower)
        if existing is not None:
            counts["subjects_reused"] += 1
            resolved_items.append((existing, item))
        else:
            dept_codes = {str(c).strip().upper() for c in (item.get("department_codes") or [])}
            is_stream_specific = bool(dept_codes) and dept_codes != {"SCI", "ART", "COMM"}
            created_subj = Subject(
                school_id=school_id,
                name=str(item["name"]).strip(),
                code=code_upper,
                is_elective=bool(
                    item.get("is_elective", False)
                    or (not separate_senior_departments and is_stream_specific)
                ),
                is_trade_subject=bool(item.get("is_trade_subject", False)),
            )
            db.add(created_subj)
            new_subjects.append(created_subj)
            subjects_by_code[code_upper] = created_subj
            subjects_by_name[name_lower] = created_subj
            counts["subjects_created"] += 1
            resolved_items.append((created_subj, item))
    if new_subjects:
        await db.flush()

    existing_offerings = (
        (
            await db.execute(
                select(SubjectOffering).where(
                    SubjectOffering.school_id == school_id,
                    SubjectOffering.academic_session_id == session_id,
                    SubjectOffering.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    offerings_by_key = {
        (o.subject_id, o.academic_level_id, o.department_id): o for o in existing_offerings
    }

    dept_lookup = {k.upper(): v for k, v in (departments_by_code or {}).items()}
    new_offerings: list[SubjectOffering] = []
    target_offerings: list[tuple[SubjectOffering, AcademicLevel, UUID | None]] = []

    for subject, item in resolved_items:
        item_categories = set(item.get("categories") or [])
        item_dept_codes = [str(c).strip().upper() for c in (item.get("department_codes") or [])]
        for level_item in preview["levels"]:
            if item_categories and level_item.get("category") not in item_categories:
                continue
            level = levels_by_code[level_item["code"]]
            is_senior_level = bool(
                level_item.get("requires_department")
                or level_item.get("category") == "SENIOR_SECONDARY"
            )
            if separate_senior_departments and is_senior_level and item_dept_codes:
                target_dept_ids: list[UUID | None] = [
                    dept_lookup[dc].id for dc in item_dept_codes if dc in dept_lookup
                ]
            else:
                target_dept_ids = [None]

            for dept_id in target_dept_ids:
                key = (subject.id, level.id, dept_id)
                offering = offerings_by_key.get(key)
                if offering is None:
                    offering = SubjectOffering(
                        school_id=school_id,
                        subject_id=subject.id,
                        academic_session_id=session_id,
                        academic_level_id=level.id,
                        department_id=dept_id,
                    )
                    db.add(offering)
                    new_offerings.append(offering)
                    offerings_by_key[key] = offering
                    counts["subject_offerings_created"] += 1
                else:
                    counts["subject_offerings_reused"] += 1
                target_offerings.append((offering, level, dept_id))
    if new_offerings:
        await db.flush()

    class_group_ids = [group.id for group in class_groups]
    existing_class_subjects: set[tuple[UUID, UUID]] = set()
    if class_group_ids:
        existing_cs_rows = (
            await db.execute(
                select(ClassSubject.subject_offering_id, ClassSubject.class_group_id).where(
                    ClassSubject.school_id == school_id,
                    ClassSubject.class_group_id.in_(class_group_ids),
                    ClassSubject.deleted_at.is_(None),
                )
            )
        ).all()
        existing_class_subjects = {(oid, cgid) for oid, cgid in existing_cs_rows}

    for offering, level, dept_id in target_offerings:
        for class_group in groups_by_level.get(level.id, []):
            if dept_id is not None and class_group.department_id != dept_id:
                continue
            if (offering.id, class_group.id) in existing_class_subjects:
                counts["class_subjects_reused"] += 1
            else:
                _, cs_created = await get_or_create_class_subject_with_status(
                    db, school_id, offering, class_group
                )
                existing_class_subjects.add((offering.id, class_group.id))
                counts["class_subjects_created" if cs_created else "class_subjects_reused"] += 1

    for class_group in class_groups:
        counts[
            "subject_registrations_created"
        ] += await backfill_compulsory_registrations_for_class_group(db, school_id, class_group)

    return counts


async def apply_structure_template(
    db: AsyncSession,
    school_id: UUID,
    req: StructureTemplateApplyRequest,
    audit: AuditContext,
) -> StructureTemplateApplyResponse:
    from app.modules.identity.models import SchoolConfiguration

    config = (
        await db.execute(
            select(SchoolConfiguration).where(
                SchoolConfiguration.school_id == school_id,
                SchoolConfiguration.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if config is not None and config.setup_wizard_completed:
        raise ConflictException(
            message=(
                "Your school's initial setup is already complete. To avoid duplicating classes, "
                "arms, or subjects, please manage changes directly in Classes & Terms and Subjects."
            ),
            code="SETUP_WIZARD_ALREADY_COMPLETED",
        )

    template_id = _resolve_structure_template_id(req)
    preview = get_structure_template_preview(
        template_id,
        arms_per_level=req.arms_per_level,
        department_arm_counts=req.department_arm_counts,
        level_arm_counts=req.level_arm_counts,
        level_department_arm_counts=req.level_department_arm_counts,
        separate_senior_departments=req.separate_senior_departments,
    )
    if preview is None:
        raise ValidationException(
            "We couldn't find that structure template. Please refresh the setup page and choose one of the available templates."
        )

    session: AcademicSession | None = None
    if req.academic_session_id is not None:
        session = (
            await db.execute(
                select(AcademicSession).where(
                    AcademicSession.id == req.academic_session_id,
                    AcademicSession.school_id == school_id,
                    AcademicSession.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if session is None:
            raise ResourceNotFoundException("academic session", req.academic_session_id)
    else:
        assert req.session_name is not None
        assert req.session_start_date is not None
        assert req.session_end_date is not None
        await _ensure_session_name_available(db, school_id, req.session_name)
        session = AcademicSession(
            school_id=school_id,
            name=req.session_name.strip(),
            start_date=req.session_start_date,
            end_date=req.session_end_date,
            is_current=True,
        )
        await _unset_current_sessions(db, school_id)
        db.add(session)
        await db.flush()

    level_rows = (
        (
            await db.execute(
                select(AcademicLevel).where(
                    AcademicLevel.school_id == school_id,
                    AcademicLevel.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    levels_by_code = {level.code: level for level in level_rows}
    used_level_orders = {level.level_order for level in level_rows}
    levels_created = 0
    for item in preview["levels"]:
        if item["code"] not in levels_by_code:
            desired_order = item["level_order"]
            if desired_order in used_level_orders:
                desired_order = max(used_level_orders, default=0) + 1
            used_level_orders.add(desired_order)
            level = AcademicLevel(
                school_id=school_id,
                name=item["name"],
                code=item["code"],
                level_order=desired_order,
                category=item["category"],
            )
            db.add(level)
            levels_by_code[item["code"]] = level
            levels_created += 1

    department_rows = (
        (
            await db.execute(
                select(Department).where(
                    Department.school_id == school_id,
                    Department.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    departments_by_code = {department.code: department for department in department_rows}
    departments_created = 0
    for item in preview["departments"]:
        if item["code"] not in departments_by_code:
            department = Department(
                school_id=school_id,
                name=item["name"],
                code=item["code"],
            )
            db.add(department)
            departments_by_code[item["code"]] = department
            departments_created += 1

    arm_rows = (
        (
            await db.execute(
                select(ClassArm).where(
                    ClassArm.school_id == school_id,
                    ClassArm.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    arms_by_name = {arm.name: arm for arm in arm_rows}
    arms_created = 0
    for arm_name in preview["arms"]:
        if arm_name not in arms_by_name:
            arm = ClassArm(school_id=school_id, name=arm_name)
            db.add(arm)
            arms_by_name[arm_name] = arm
            arms_created += 1

    # New levels/departments/arms are only assigned a real id once flushed; the
    # class-group loop below needs those ids to build FK values and the dedup key.
    await db.flush()

    class_groups_created = 0
    class_groups_reused = 0
    if req.include_class_groups:
        existing_groups = (
            (
                await db.execute(
                    select(ClassGroup).where(
                        ClassGroup.school_id == school_id,
                        ClassGroup.academic_session_id == session.id,
                        ClassGroup.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        group_keys = {
            (group.academic_level_id, group.department_id, group.class_arm_id)
            for group in existing_groups
        }
        for item in preview["class_groups"]:
            level = levels_by_code[item["level_code"]]
            department = (
                departments_by_code[item["department_code"]]
                if item["department_code"] is not None
                else None
            )
            arm = arms_by_name[item["arm_name"]]
            key = (level.id, department.id if department else None, arm.id)
            if key in group_keys:
                class_groups_reused += 1
                continue
            department_name = f" {department.name}" if department is not None else ""
            db.add(
                ClassGroup(
                    school_id=school_id,
                    academic_session_id=session.id,
                    academic_level_id=level.id,
                    department_id=department.id if department is not None else None,
                    class_arm_id=arm.id,
                    display_name=f"{level.name}{department_name} {arm.name}".strip(),
                )
            )
            group_keys.add(key)
            class_groups_created += 1

    await db.flush()
    subject_counts = await _provision_template_subjects(
        db,
        school_id,
        session.id,
        preview,
        levels_by_code,
        departments_by_code=departments_by_code,
        selected_subject_codes=req.selected_subject_codes,
        separate_senior_departments=req.separate_senior_departments,
    )
    record_audit_log(
        db,
        audit,
        action="STRUCTURE_TEMPLATE_APPLIED",
        resource_type="AcademicSession",
        resource_id=session.id,
        diff_payload={
            "template_id": template_id,
            "template_type": preview["template_type"],
            "levels_created": levels_created,
            "departments_created": departments_created,
            "arms_created": arms_created,
            "class_groups_created": class_groups_created,
            **subject_counts,
        },
    )
    await _commit_or_conflict(
        db,
        message=(
            "The structure template could not be applied. Please review your existing classes "
            "and try again."
        ),
        code="STRUCTURE_TEMPLATE_CONFLICT",
    )

    return StructureTemplateApplyResponse(
        template_id=template_id,
        template_type=StructureTemplateType(preview["template_type"]),
        academic_session_id=session.id,
        levels_created=levels_created,
        levels_reused=len(preview["levels"]) - levels_created,
        departments_created=departments_created,
        departments_reused=len(preview["departments"]) - departments_created,
        arms_created=arms_created,
        arms_reused=len(preview["arms"]) - arms_created,
        class_groups_created=class_groups_created,
        class_groups_reused=class_groups_reused,
        **subject_counts,
        message=("The structure template has been applied. Existing records were kept unchanged."),
    )


def _format_rollover_summary(
    source_name: str,
    target_name: str,
    *,
    class_groups_created: int,
    subject_offerings_created: int,
    class_subjects_created: int,
    teacher_assignments_created: int,
    skipped_inactive_teachers: int,
) -> str:
    parts: list[str] = []
    if class_groups_created:
        parts.append(
            f"{class_groups_created} class {'arm' if class_groups_created == 1 else 'arms'}"
        )
    if subject_offerings_created:
        label = "subject offering" if subject_offerings_created == 1 else "subject offerings"
        parts.append(f"{subject_offerings_created} {label}")
    if class_subjects_created:
        label = "class subject" if class_subjects_created == 1 else "class subjects"
        parts.append(f"{class_subjects_created} {label}")
    if teacher_assignments_created:
        label = "teacher assignment" if teacher_assignments_created == 1 else "teacher assignments"
        parts.append(f"{teacher_assignments_created} {label}")

    if parts:
        if len(parts) > 1:
            joined = ", ".join(parts[:-1]) + f" and {parts[-1]}"
        else:
            joined = parts[0]
        base = f"{target_name} is now set up from {source_name}: added {joined}."
    else:
        base = (
            f"{target_name} already has all matching classes and subjects from {source_name} — "
            "nothing new needed to be added."
        )

    if skipped_inactive_teachers > 0:
        noun = "teacher was" if skipped_inactive_teachers == 1 else "teachers were"
        base += (
            f" Note: {skipped_inactive_teachers} inactive or former {noun} skipped so you can "
            "assign active staff."
        )
    return base


async def rollover_session(
    db: AsyncSession,
    school_id: UUID,
    target_session_id: UUID,
    req: SessionRolloverRequest,
    actor_user_id: UUID,
    audit: AuditContext,
) -> SessionRolloverResponse:
    """Idempotently copy class arms, offerings, class subjects, and active teachers into a session."""
    if req.source_session_id == target_session_id:
        raise ValidationException(
            "Please choose a different previous session to copy from.",
            code="INVALID_ROLLOVER_SOURCE",
        )

    sessions = (
        (
            await db.execute(
                select(AcademicSession).where(
                    AcademicSession.school_id == school_id,
                    AcademicSession.id.in_([req.source_session_id, target_session_id]),
                    AcademicSession.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    sessions_by_id = {s.id: s for s in sessions}
    source_session = sessions_by_id.get(req.source_session_id)
    target_session = sessions_by_id.get(target_session_id)
    if source_session is None or target_session is None:
        missing_id = target_session_id if target_session is None else req.source_session_id
        raise ResourceNotFoundException("Academic session", str(missing_id))

    active_user_ids = set(
        (
            await db.execute(
                select(User.id).where(
                    User.school_id == school_id,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    active_level_ids = set(
        (
            await db.execute(
                select(AcademicLevel.id).where(
                    AcademicLevel.school_id == school_id,
                    AcademicLevel.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    active_dept_ids = set(
        (
            await db.execute(
                select(Department.id).where(
                    Department.school_id == school_id,
                    Department.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    active_arm_ids = set(
        (
            await db.execute(
                select(ClassArm.id).where(
                    ClassArm.school_id == school_id,
                    ClassArm.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    class_groups_created = 0
    class_groups_reused = 0
    subject_offerings_created = 0
    subject_offerings_reused = 0
    class_subjects_created = 0
    class_subjects_reused = 0
    teacher_assignments_created = 0
    skipped_inactive_teachers = 0

    group_map: dict[UUID, ClassGroup] = {}
    target_groups_by_key: dict[tuple[UUID, UUID | None, UUID], ClassGroup] = {}

    if req.copy_class_groups or req.copy_class_subjects:
        source_groups = (
            (
                await db.execute(
                    select(ClassGroup).where(
                        ClassGroup.school_id == school_id,
                        ClassGroup.academic_session_id == source_session.id,
                        ClassGroup.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        target_groups = (
            (
                await db.execute(
                    select(ClassGroup).where(
                        ClassGroup.school_id == school_id,
                        ClassGroup.academic_session_id == target_session.id,
                        ClassGroup.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        target_groups_by_key = {
            (g.academic_level_id, g.department_id, g.class_arm_id): g for g in target_groups
        }

        for src_group in source_groups:
            if (
                src_group.academic_level_id not in active_level_ids
                or src_group.class_arm_id not in active_arm_ids
                or (
                    src_group.department_id is not None
                    and src_group.department_id not in active_dept_ids
                )
            ):
                continue

            key = (
                src_group.academic_level_id,
                src_group.department_id,
                src_group.class_arm_id,
            )
            existing_target = target_groups_by_key.get(key)
            if existing_target is not None:
                if (
                    req.copy_class_teachers
                    and existing_target.class_teacher_id is None
                    and src_group.class_teacher_id is not None
                ):
                    if src_group.class_teacher_id in active_user_ids:
                        existing_target.class_teacher_id = src_group.class_teacher_id
                    else:
                        skipped_inactive_teachers += 1
                group_map[src_group.id] = existing_target
                class_groups_reused += 1
                continue

            if not req.copy_class_groups:
                continue

            teacher_id: UUID | None = None
            if req.copy_class_teachers and src_group.class_teacher_id is not None:
                if src_group.class_teacher_id in active_user_ids:
                    teacher_id = src_group.class_teacher_id
                else:
                    skipped_inactive_teachers += 1

            new_group = ClassGroup(
                school_id=school_id,
                academic_session_id=target_session.id,
                academic_level_id=src_group.academic_level_id,
                department_id=src_group.department_id,
                class_arm_id=src_group.class_arm_id,
                class_teacher_id=teacher_id,
                display_name=src_group.display_name,
            )
            db.add(new_group)
            target_groups_by_key[key] = new_group
            group_map[src_group.id] = new_group
            class_groups_created += 1

        await db.flush()

    offering_map: dict[UUID, SubjectOffering] = {}
    if req.copy_subject_offerings or req.copy_class_subjects:
        active_subject_ids = set(
            (
                await db.execute(
                    select(Subject.id).where(
                        Subject.school_id == school_id,
                        Subject.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        source_offerings = (
            (
                await db.execute(
                    select(SubjectOffering).where(
                        SubjectOffering.school_id == school_id,
                        SubjectOffering.academic_session_id == source_session.id,
                        SubjectOffering.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        target_offerings = (
            (
                await db.execute(
                    select(SubjectOffering).where(
                        SubjectOffering.school_id == school_id,
                        SubjectOffering.academic_session_id == target_session.id,
                        SubjectOffering.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        target_offerings_by_key = {
            (o.subject_id, o.academic_level_id, o.department_id): o for o in target_offerings
        }

        for src_off in source_offerings:
            if (
                src_off.subject_id not in active_subject_ids
                or src_off.academic_level_id not in active_level_ids
                or (
                    src_off.department_id is not None
                    and src_off.department_id not in active_dept_ids
                )
            ):
                continue

            key = (src_off.subject_id, src_off.academic_level_id, src_off.department_id)
            existing_off = target_offerings_by_key.get(key)
            if existing_off is not None:
                offering_map[src_off.id] = existing_off
                subject_offerings_reused += 1
                continue

            if not req.copy_subject_offerings:
                continue

            new_off = SubjectOffering(
                school_id=school_id,
                subject_id=src_off.subject_id,
                academic_session_id=target_session.id,
                academic_level_id=src_off.academic_level_id,
                department_id=src_off.department_id,
            )
            db.add(new_off)
            target_offerings_by_key[key] = new_off
            offering_map[src_off.id] = new_off
            subject_offerings_created += 1

        await db.flush()

    if req.copy_class_subjects and group_map and offering_map:
        source_cs_list = (
            (
                await db.execute(
                    select(ClassSubject).where(
                        ClassSubject.school_id == school_id,
                        ClassSubject.class_group_id.in_(list(group_map.keys())),
                        ClassSubject.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        target_group_ids = [g.id for g in target_groups_by_key.values()]
        target_cs_list = (
            (
                await db.execute(
                    select(ClassSubject).where(
                        ClassSubject.school_id == school_id,
                        ClassSubject.class_group_id.in_(target_group_ids),
                        ClassSubject.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        target_cs_by_key = {
            (cs.subject_offering_id, cs.class_group_id): cs for cs in target_cs_list
        }
        cs_map: dict[UUID, ClassSubject] = {}

        for src_cs in source_cs_list:
            tgt_group = group_map.get(src_cs.class_group_id)
            tgt_off = offering_map.get(src_cs.subject_offering_id)
            if tgt_group is None or tgt_off is None:
                continue

            key = (tgt_off.id, tgt_group.id)
            existing_cs = target_cs_by_key.get(key)
            if existing_cs is not None:
                cs_map[src_cs.id] = existing_cs
                class_subjects_reused += 1
                continue

            new_cs = ClassSubject(
                school_id=school_id,
                subject_offering_id=tgt_off.id,
                class_group_id=tgt_group.id,
            )
            db.add(new_cs)
            target_cs_by_key[key] = new_cs
            cs_map[src_cs.id] = new_cs
            class_subjects_created += 1

        await db.flush()
        for cs in cs_map.values():
            await seed_assessment_definitions_for_class_subject(db, school_id, cs)

        if req.copy_subject_teachers and cs_map:
            src_assignments = (
                (
                    await db.execute(
                        select(TeacherAssignment).where(
                            TeacherAssignment.school_id == school_id,
                            TeacherAssignment.class_subject_id.in_(list(cs_map.keys())),
                            TeacherAssignment.is_active.is_(True),
                            TeacherAssignment.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            tgt_cs_ids = [cs.id for cs in cs_map.values()]
            existing_tgt_assignments = (
                (
                    await db.execute(
                        select(TeacherAssignment).where(
                            TeacherAssignment.school_id == school_id,
                            TeacherAssignment.class_subject_id.in_(tgt_cs_ids),
                            TeacherAssignment.is_active.is_(True),
                            TeacherAssignment.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            existing_pairs = {
                (ta.class_subject_id, ta.teacher_id) for ta in existing_tgt_assignments
            }

            for src_ta in src_assignments:
                tgt_cs = cs_map.get(src_ta.class_subject_id)
                if tgt_cs is None:
                    continue
                if src_ta.teacher_id not in active_user_ids:
                    skipped_inactive_teachers += 1
                    continue
                pair = (tgt_cs.id, src_ta.teacher_id)
                if pair in existing_pairs:
                    continue

                db.add(
                    TeacherAssignment(
                        school_id=school_id,
                        teacher_id=src_ta.teacher_id,
                        class_subject_id=tgt_cs.id,
                        assigned_by_id=actor_user_id,
                        is_active=True,
                        start_date=target_session.start_date,
                    )
                )
                existing_pairs.add(pair)
                teacher_assignments_created += 1

    message = _format_rollover_summary(
        source_session.name,
        target_session.name,
        class_groups_created=class_groups_created,
        subject_offerings_created=subject_offerings_created,
        class_subjects_created=class_subjects_created,
        teacher_assignments_created=teacher_assignments_created,
        skipped_inactive_teachers=skipped_inactive_teachers,
    )

    record_audit_log(
        db,
        audit,
        action="SESSION_ROLLOVER",
        resource_type="AcademicSession",
        resource_id=target_session.id,
        diff_payload={
            "source_session_id": str(source_session.id),
            "source_session_name": source_session.name,
            "target_session_name": target_session.name,
            "class_groups_created": class_groups_created,
            "class_groups_reused": class_groups_reused,
            "subject_offerings_created": subject_offerings_created,
            "subject_offerings_reused": subject_offerings_reused,
            "class_subjects_created": class_subjects_created,
            "class_subjects_reused": class_subjects_reused,
            "teacher_assignments_created": teacher_assignments_created,
            "skipped_inactive_teachers": skipped_inactive_teachers,
        },
    )
    await _commit_or_conflict(
        db,
        message=(
            "We couldn't finish copying classes and subjects into this session. Please review "
            "your existing setup and try again."
        ),
        code="SESSION_ROLLOVER_CONFLICT",
    )

    return SessionRolloverResponse(
        source_session_id=source_session.id,
        target_session_id=target_session.id,
        source_session_name=source_session.name,
        target_session_name=target_session.name,
        class_groups_created=class_groups_created,
        class_groups_reused=class_groups_reused,
        subject_offerings_created=subject_offerings_created,
        subject_offerings_reused=subject_offerings_reused,
        class_subjects_created=class_subjects_created,
        class_subjects_reused=class_subjects_reused,
        teacher_assignments_created=teacher_assignments_created,
        skipped_inactive_teachers=skipped_inactive_teachers,
        message=message,
    )
