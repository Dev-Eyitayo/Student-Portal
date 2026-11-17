from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    InsufficientPermissionException,
    ResourceNotFoundException,
    ScoresIncompleteException,
    ValidationException,
)
from app.core.permissions import (
    build_scope_filter,
    get_permission_scope,
    require_scoped_permission,
    user_has_permission,
)
from app.modules.academic_structure.models import AcademicTerm, ClassGroup
from app.modules.assessments.models import AssessmentDefinition, AssessmentScore, ScoreSheetStatus
from app.modules.assessments.schemas import (
    AssessmentDefinitionCreateRequest,
    AssessmentDefinitionUpdateRequest,
    BulkScoreEntryAssessmentSummary,
    BulkScoreEntryRequest,
    BulkScoreEntryResponse,
    ScoreEntryError,
    ScoreSheetRow,
    ScoreStatus,
)
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.common.pagination import paginate
from app.modules.common.schemas import PageMeta
from app.modules.identity.models import SchoolConfiguration, User
from app.modules.students.models import Student, StudentEnrollment
from app.modules.subjects.models import (
    ClassSubject,
    StudentSubjectRegistration,
    Subject,
    SubjectOffering,
)

ASSESSMENT_TYPE_BY_CODE = {
    "TEST1": "CONTINUOUS_ASSESSMENT",
    "TEST2": "CONTINUOUS_ASSESSMENT",
    "ASSIGN": "CONTINUOUS_ASSESSMENT",
    "EXAM": "EXAMINATION",
}
TEACHER_OWNERSHIP_PERMISSIONS = {"scores:enter", "scores:submit"}


async def _commit_or_conflict(
    db: AsyncSession,
    *,
    message: str,
    code: str = "ASSESSMENT_CONFLICT",
) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictException(message=message, code=code) from exc


async def seed_assessment_definitions_for_class_subject(
    db: AsyncSession,
    school_id: UUID,
    class_subject: ClassSubject,
) -> list[AssessmentDefinition]:
    """Clones the school's active assessment weight schema into concrete
    AssessmentDefinition rows for every term in the class subject's session.
    Called from subjects.services when a new ClassSubject is created.
    """
    offering_stmt = select(SubjectOffering).where(
        SubjectOffering.id == class_subject.subject_offering_id
    )
    offering = (await db.execute(offering_stmt)).scalar_one()

    terms_stmt = select(AcademicTerm).where(
        AcademicTerm.academic_session_id == offering.academic_session_id,
        AcademicTerm.deleted_at.is_(None),
    )
    terms = (await db.execute(terms_stmt)).scalars().all()

    config_stmt = select(SchoolConfiguration).where(SchoolConfiguration.school_id == school_id)
    config = (await db.execute(config_stmt)).scalar_one_or_none()
    if not config or not terms:
        return []

    created: list[AssessmentDefinition] = []
    for term in terms:
        for order, (code, spec) in enumerate(config.assessment_weight_schema.items(), start=1):
            existing_stmt = select(AssessmentDefinition.id).where(
                AssessmentDefinition.academic_term_id == term.id,
                AssessmentDefinition.class_subject_id == class_subject.id,
                AssessmentDefinition.code == code,
            )
            if (await db.execute(existing_stmt)).scalar_one_or_none():
                continue
            definition = AssessmentDefinition(
                school_id=school_id,
                academic_term_id=term.id,
                class_subject_id=class_subject.id,
                name=spec["name"],
                code=code,
                assessment_type=ASSESSMENT_TYPE_BY_CODE.get(code, "CONTINUOUS_ASSESSMENT"),
                max_score=Decimal(str(spec["max_score"])),
                weight_percentage=Decimal(str(spec["weight_percentage"])),
                sequence_order=order,
            )
            db.add(definition)
            created.append(definition)

    if created:
        await db.flush()
    return created


async def _get_definition(
    db: AsyncSession, school_id: UUID, definition_id: UUID
) -> AssessmentDefinition:
    stmt = select(AssessmentDefinition).where(
        AssessmentDefinition.id == definition_id, AssessmentDefinition.school_id == school_id
    )
    definition = (await db.execute(stmt)).scalar_one_or_none()
    if not definition:
        raise ResourceNotFoundException("assessment definition", definition_id)
    return definition


async def _get_class_subject(
    db: AsyncSession, school_id: UUID, class_subject_id: UUID
) -> ClassSubject:
    stmt = select(ClassSubject).where(
        ClassSubject.id == class_subject_id,
        ClassSubject.school_id == school_id,
        ClassSubject.deleted_at.is_(None),
    )
    class_subject = (await db.execute(stmt)).scalar_one_or_none()
    if not class_subject:
        raise ResourceNotFoundException("class subject", class_subject_id)
    return class_subject


async def _get_term(db: AsyncSession, school_id: UUID, academic_term_id: UUID) -> AcademicTerm:
    stmt = select(AcademicTerm).where(
        AcademicTerm.id == academic_term_id, AcademicTerm.school_id == school_id
    )
    term = (await db.execute(stmt)).scalar_one_or_none()
    if not term:
        raise ResourceNotFoundException("academic term", academic_term_id)
    return term


async def _check_class_subject_access(
    db: AsyncSession,
    school_id: UUID,
    class_subject: ClassSubject,
    current_user: User,
    permission_code: str,
) -> None:
    """
    Deliberately does not delegate straight to require_scoped_permission: a
    TEACHER's role-default scores:enter/scores:submit is ownership-gated (only
    for subjects they are actually assigned to), not an unscoped baseline the
    way ACADEMIC_OFFICER's role defaults are. Calling require_scoped_permission
    directly would treat that role-default as unscoped and let any teacher
    touch any class subject's scores, defeating the ownership check entirely.
    """
    from app.modules.subjects.services import is_teacher_assigned_to_class_subject

    if current_user.role in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        return
    if current_user.role == "ACADEMIC_OFFICER":
        if await user_has_permission(db, current_user, permission_code):
            return
    if permission_code in TEACHER_OWNERSHIP_PERMISSIONS and current_user.role == "TEACHER":
        if await is_teacher_assigned_to_class_subject(
            db, school_id, current_user.id, class_subject.id
        ):
            return

    class_group_stmt = select(ClassGroup).where(ClassGroup.id == class_subject.class_group_id)
    class_group = (await db.execute(class_group_stmt)).scalar_one()
    allowed = await user_has_permission(
        db,
        current_user,
        permission_code,
        department_id=class_group.department_id,
        academic_level_id=class_group.academic_level_id,
        class_group_id=class_group.id,
    )
    if not allowed:
        raise InsufficientPermissionException(permission_code=permission_code)


# --------------------------------------------------------------------------
# Assessment Definitions
# --------------------------------------------------------------------------


async def list_definitions(
    db: AsyncSession,
    school_id: UUID,
    current_user: User,
    *,
    academic_term_id: UUID | None,
    class_subject_id: UUID | None,
    page: int,
    page_size: int,
) -> tuple[list[AssessmentDefinition], PageMeta]:
    scope = await get_permission_scope(db, current_user, "subjects:manage")
    if not scope.has_any_access:
        raise InsufficientPermissionException(permission_code="subjects:manage")

    stmt: Select[tuple[AssessmentDefinition]] = select(AssessmentDefinition).where(
        AssessmentDefinition.school_id == school_id
    )
    if academic_term_id is not None:
        stmt = stmt.where(AssessmentDefinition.academic_term_id == academic_term_id)
    if class_subject_id is not None:
        stmt = stmt.where(AssessmentDefinition.class_subject_id == class_subject_id)
    if not scope.unscoped:
        stmt = (
            stmt.join(ClassSubject, ClassSubject.id == AssessmentDefinition.class_subject_id)
            .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
            .where(
                build_scope_filter(
                    scope,
                    department_column=ClassGroup.department_id,
                    academic_level_column=ClassGroup.academic_level_id,
                    class_group_column=ClassGroup.id,
                )
            )
        )
    stmt = stmt.order_by(AssessmentDefinition.sequence_order.asc())
    return await paginate(db, stmt, page=page, page_size=page_size)


async def _require_class_subject_scope(
    db: AsyncSession,
    current_user: User,
    class_subject: ClassSubject,
    permission_code: str = "subjects:manage",
) -> None:
    class_group_stmt = select(ClassGroup).where(ClassGroup.id == class_subject.class_group_id)
    class_group = (await db.execute(class_group_stmt)).scalar_one()
    await require_scoped_permission(
        db,
        current_user,
        permission_code,
        department_id=class_group.department_id,
        academic_level_id=class_group.academic_level_id,
        class_group_id=class_group.id,
    )


async def create_definition(
    db: AsyncSession,
    school_id: UUID,
    req: AssessmentDefinitionCreateRequest,
    current_user: User,
    audit: AuditContext,
) -> AssessmentDefinition:
    await _get_term(db, school_id, req.academic_term_id)
    class_subject = await _get_class_subject(db, school_id, req.class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject)

    definition = AssessmentDefinition(
        school_id=school_id,
        academic_term_id=req.academic_term_id,
        class_subject_id=req.class_subject_id,
        name=req.name.strip(),
        code=req.code.strip().upper(),
        assessment_type=req.assessment_type.value,
        max_score=req.max_score,
        weight_percentage=req.weight_percentage,
        sequence_order=req.sequence_order,
    )
    db.add(definition)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ASSESSMENT_DEFINITION_CREATED",
        resource_type="AssessmentDefinition",
        resource_id=definition.id,
        diff_payload={"after": {"code": definition.code, "max_score": definition.max_score}},
    )
    await _commit_or_conflict(
        db,
        message=(
            "An assessment component with this code already exists for this class subject and term."
        ),
        code="ASSESSMENT_DEFINITION_CONFLICT",
    )
    await db.refresh(definition)
    return definition


async def get_definition(
    db: AsyncSession, school_id: UUID, definition_id: UUID, current_user: User
) -> AssessmentDefinition:
    definition = await _get_definition(db, school_id, definition_id)
    class_subject = await _get_class_subject(db, school_id, definition.class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject)
    return definition


async def update_definition(
    db: AsyncSession,
    school_id: UUID,
    definition_id: UUID,
    req: AssessmentDefinitionUpdateRequest,
    current_user: User,
    audit: AuditContext,
) -> AssessmentDefinition:
    definition = await _get_definition(db, school_id, definition_id)
    class_subject = await _get_class_subject(db, school_id, definition.class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject)

    score_count = await db.execute(
        select(func.count()).where(AssessmentScore.assessment_definition_id == definition.id)
    )
    if score_count.scalar_one() > 0:
        raise ValidationException(
            "This assessment component already has recorded scores, so its weighting cannot "
            "be changed. Please correct scores instead, or make this change from a new term."
        )

    changes = req.model_dump(exclude_unset=True)
    if req.name is not None:
        definition.name = req.name.strip()
    if req.max_score is not None:
        definition.max_score = req.max_score
    if req.weight_percentage is not None:
        definition.weight_percentage = req.weight_percentage
    if req.sequence_order is not None:
        definition.sequence_order = req.sequence_order

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ASSESSMENT_DEFINITION_UPDATED",
        resource_type="AssessmentDefinition",
        resource_id=definition.id,
        diff_payload={"changes": changes},
    )
    await _commit_or_conflict(
        db,
        message="This assessment component could not be saved. Please try again.",
        code="ASSESSMENT_DEFINITION_CONFLICT",
    )
    await db.refresh(definition)
    return definition


# --------------------------------------------------------------------------
# Score Sheet (read + bulk entry)
# --------------------------------------------------------------------------


async def get_score_sheet(
    db: AsyncSession,
    school_id: UUID,
    class_subject_id: UUID,
    assessment_definition_id: UUID,
    current_user: User,
) -> tuple[AssessmentDefinition, list[ScoreSheetRow]]:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    definition = await _get_definition(db, school_id, assessment_definition_id)
    if definition.class_subject_id != class_subject.id:
        raise ValidationException(
            "This assessment component does not belong to the selected class subject."
        )
    await _check_class_subject_access(db, school_id, class_subject, current_user, "scores:enter")

    roster_stmt = (
        select(Student, StudentEnrollment.id, AssessmentScore)
        .select_from(StudentSubjectRegistration)
        .join(
            StudentEnrollment,
            StudentEnrollment.id == StudentSubjectRegistration.student_enrollment_id,
        )
        .join(Student, Student.id == StudentEnrollment.student_id)
        .outerjoin(
            AssessmentScore,
            (AssessmentScore.student_enrollment_id == StudentEnrollment.id)
            & (AssessmentScore.assessment_definition_id == definition.id),
        )
        .where(
            StudentSubjectRegistration.class_subject_id == class_subject.id,
            StudentSubjectRegistration.is_active.is_(True),
        )
        .order_by(Student.last_name.asc(), Student.first_name.asc())
    )
    rows = (await db.execute(roster_stmt)).all()

    sheet_rows = [
        ScoreSheetRow(
            student_enrollment_id=enrollment_id,
            admission_number=student.admission_number,
            first_name=student.first_name,
            last_name=student.last_name,
            score_status=ScoreStatus(score.score_status) if score else ScoreStatus.PENDING,
            raw_score=score.raw_score if score else None,
        )
        for student, enrollment_id, score in rows
    ]
    return definition, sheet_rows


async def bulk_score_entry(
    db: AsyncSession,
    school_id: UUID,
    req: BulkScoreEntryRequest,
    current_user: User,
    audit: AuditContext,
) -> BulkScoreEntryResponse:
    class_subject = await _get_class_subject(db, school_id, req.class_subject_id)
    definition = await _get_definition(db, school_id, req.assessment_definition_id)
    if definition.class_subject_id != class_subject.id:
        raise ValidationException(
            "This assessment component does not belong to the selected class subject."
        )
    await _check_class_subject_access(db, school_id, class_subject, current_user, "scores:enter")

    sheet_status = await _get_or_create_score_sheet_status(
        db, school_id, definition.academic_term_id, class_subject.id
    )
    if sheet_status.status != "DRAFT":
        raise ValidationException(
            "These scores are locked for review and can't be edited. Ask your academic "
            "officer to unlock them if a correction is needed."
        )

    registered_stmt = select(StudentSubjectRegistration.student_enrollment_id).where(
        StudentSubjectRegistration.class_subject_id == class_subject.id,
        StudentSubjectRegistration.is_active.is_(True),
    )
    registered_ids = set((await db.execute(registered_stmt)).scalars().all())

    errors: list[ScoreEntryError] = []
    updated_count = 0
    for item in req.scores:
        if item.student_enrollment_id not in registered_ids:
            errors.append(
                ScoreEntryError(
                    student_enrollment_id=item.student_enrollment_id,
                    message="This student is not registered for this class subject.",
                )
            )
            continue
        if item.raw_score is not None and item.raw_score > definition.max_score:
            errors.append(
                ScoreEntryError(
                    student_enrollment_id=item.student_enrollment_id,
                    message=(
                        f"Score {item.raw_score} exceeds the configured maximum permitted "
                        f"score of {definition.max_score} for {definition.name}."
                    ),
                )
            )
            continue

        existing_stmt = select(AssessmentScore).where(
            AssessmentScore.assessment_definition_id == definition.id,
            AssessmentScore.student_enrollment_id == item.student_enrollment_id,
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            existing.score_status = item.score_status.value
            existing.raw_score = item.raw_score
            existing.last_modified_by_id = current_user.id
        else:
            db.add(
                AssessmentScore(
                    school_id=school_id,
                    assessment_definition_id=definition.id,
                    student_enrollment_id=item.student_enrollment_id,
                    score_status=item.score_status.value,
                    raw_score=item.raw_score,
                    recorded_by_id=current_user.id,
                    last_modified_by_id=current_user.id,
                )
            )
        updated_count += 1

    await db.flush()
    label_stmt = (
        select(Subject.name, ClassGroup.display_name)
        .join(SubjectOffering, SubjectOffering.subject_id == Subject.id)
        .join(ClassGroup, ClassGroup.id == class_subject.class_group_id)
        .where(SubjectOffering.id == class_subject.subject_offering_id)
    )
    subject_name, class_group_name = (await db.execute(label_stmt)).one()
    record_audit_log(
        db,
        audit,
        action="SCORE_ENTRY_BULK",
        resource_type="AssessmentDefinition",
        resource_id=definition.id,
        diff_payload={"records_updated": updated_count, "errors": [e.message for e in errors]},
    )
    await _commit_or_conflict(
        db,
        message="These scores could not be saved. Please try again.",
        code="SCORE_ENTRY_CONFLICT",
    )

    return BulkScoreEntryResponse(
        records_processed=len(req.scores),
        records_updated=updated_count,
        errors=errors,
        assessment_summary=BulkScoreEntryAssessmentSummary(
            assessment_name=definition.name,
            max_score=definition.max_score,
            class_subject=f"{subject_name} - {class_group_name}",
        ),
    )


# --------------------------------------------------------------------------
# Score Sheet Lifecycle (DRAFT -> SUBMITTED -> APPROVED)
# --------------------------------------------------------------------------


async def _get_or_create_score_sheet_status(
    db: AsyncSession,
    school_id: UUID,
    academic_term_id: UUID,
    class_subject_id: UUID,
) -> ScoreSheetStatus:
    stmt = select(ScoreSheetStatus).where(
        ScoreSheetStatus.academic_term_id == academic_term_id,
        ScoreSheetStatus.class_subject_id == class_subject_id,
    )
    status_row = (await db.execute(stmt)).scalar_one_or_none()
    if status_row:
        return status_row

    status_row = ScoreSheetStatus(
        school_id=school_id,
        academic_term_id=academic_term_id,
        class_subject_id=class_subject_id,
    )
    db.add(status_row)
    await db.flush()
    return status_row


async def submit_score_sheet(
    db: AsyncSession,
    school_id: UUID,
    academic_term_id: UUID,
    class_subject_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> ScoreSheetStatus:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    await _get_term(db, school_id, academic_term_id)
    await _check_class_subject_access(db, school_id, class_subject, current_user, "scores:submit")

    definitions_stmt = select(AssessmentDefinition.id).where(
        AssessmentDefinition.academic_term_id == academic_term_id,
        AssessmentDefinition.class_subject_id == class_subject_id,
    )
    definition_ids = list((await db.execute(definitions_stmt)).scalars().all())

    registered_stmt = select(StudentSubjectRegistration.student_enrollment_id).where(
        StudentSubjectRegistration.class_subject_id == class_subject_id,
        StudentSubjectRegistration.is_active.is_(True),
    )
    enrollment_ids = list((await db.execute(registered_stmt)).scalars().all())

    expected = len(definition_ids) * len(enrollment_ids)
    if expected > 0:
        recorded_stmt = select(func.count()).where(
            AssessmentScore.assessment_definition_id.in_(definition_ids),
            AssessmentScore.student_enrollment_id.in_(enrollment_ids),
            AssessmentScore.score_status != "PENDING",
        )
        recorded = (await db.execute(recorded_stmt)).scalar_one()
        missing = expected - recorded
        if missing > 0:
            subject_name_stmt = (
                select(Subject.name)
                .join(SubjectOffering, SubjectOffering.subject_id == Subject.id)
                .where(SubjectOffering.id == class_subject.subject_offering_id)
            )
            subject_name = (await db.execute(subject_name_stmt)).scalar_one()
            raise ScoresIncompleteException(subject_name=subject_name, missing_count=missing)

    sheet_status = await _get_or_create_score_sheet_status(
        db, school_id, academic_term_id, class_subject_id
    )
    if sheet_status.status != "DRAFT":
        raise ValidationException("This score sheet has already been submitted.")

    sheet_status.status = "SUBMITTED"
    sheet_status.submitted_by_id = current_user.id
    sheet_status.submitted_at = datetime.now(UTC)

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SCORES_SUBMITTED",
        resource_type="ScoreSheetStatus",
        resource_id=sheet_status.id,
        diff_payload={"after": {"status": "SUBMITTED"}},
    )
    await _commit_or_conflict(
        db,
        message="This score sheet could not be submitted. Please try again.",
        code="SCORE_SHEET_CONFLICT",
    )
    await db.refresh(sheet_status)
    return sheet_status


async def approve_score_sheet(
    db: AsyncSession,
    school_id: UUID,
    academic_term_id: UUID,
    class_subject_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> ScoreSheetStatus:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    await _get_term(db, school_id, academic_term_id)
    await _check_class_subject_access(db, school_id, class_subject, current_user, "scores:approve")

    sheet_status = await _get_or_create_score_sheet_status(
        db, school_id, academic_term_id, class_subject_id
    )
    if sheet_status.status != "SUBMITTED":
        raise ValidationException("This score sheet must be submitted before it can be approved.")

    sheet_status.status = "APPROVED"
    sheet_status.approved_by_id = current_user.id
    sheet_status.approved_at = datetime.now(UTC)

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SCORES_APPROVED",
        resource_type="ScoreSheetStatus",
        resource_id=sheet_status.id,
        diff_payload={"after": {"status": "APPROVED"}},
    )
    await _commit_or_conflict(
        db,
        message="This score sheet could not be approved. Please try again.",
        code="SCORE_SHEET_CONFLICT",
    )
    await db.refresh(sheet_status)
    return sheet_status


async def unlock_score_sheet(
    db: AsyncSession,
    school_id: UUID,
    academic_term_id: UUID,
    class_subject_id: UUID,
    reason: str,
    current_user: User,
    audit: AuditContext,
) -> ScoreSheetStatus:
    class_subject = await _get_class_subject(db, school_id, class_subject_id)
    await _get_term(db, school_id, academic_term_id)
    await _check_class_subject_access(db, school_id, class_subject, current_user, "scores:unlock")

    sheet_status = await _get_or_create_score_sheet_status(
        db, school_id, academic_term_id, class_subject_id
    )
    if sheet_status.status == "DRAFT":
        raise ValidationException("This score sheet is already open for editing.")

    previous_status = sheet_status.status
    sheet_status.status = "DRAFT"
    sheet_status.unlocked_by_id = current_user.id
    sheet_status.unlocked_at = datetime.now(UTC)
    sheet_status.unlock_reason = reason.strip()

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SCORES_UNLOCKED",
        resource_type="ScoreSheetStatus",
        resource_id=sheet_status.id,
        diff_payload={
            "before": {"status": previous_status},
            "after": {"status": "DRAFT", "reason": sheet_status.unlock_reason},
        },
    )
    await _commit_or_conflict(
        db,
        message="This score sheet could not be unlocked. Please try again.",
        code="SCORE_SHEET_CONFLICT",
    )
    await db.refresh(sheet_status)
    return sheet_status
