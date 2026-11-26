import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any
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
from app.modules.academic_structure.models import (
    AcademicLevel,
    AcademicSession,
    AcademicTerm,
    ClassGroup,
)
from app.modules.assessments import mark_book
from app.modules.assessments.models import AssessmentDefinition, AssessmentScore, ScoreSheetStatus
from app.modules.assessments.schemas import (
    AssessmentDefinitionBulkActionResponse,
    AssessmentDefinitionBulkCreateRequest,
    AssessmentDefinitionBulkRemoveRequest,
    AssessmentDefinitionBulkUpdateRequest,
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
from app.modules.computation import engine
from app.modules.identity.models import School, SchoolConfiguration, User
from app.modules.students.models import Student, StudentEnrollment
from app.modules.subjects.models import (
    ClassSubject,
    StudentSubjectRegistration,
    Subject,
    SubjectOffering,
    TeacherAssignment,
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


def _build_assessment_definition(
    school_id: UUID,
    term_id: UUID,
    class_subject_id: UUID,
    code: str,
    spec: dict[str, Any],
    order: int,
) -> AssessmentDefinition:
    return AssessmentDefinition(
        school_id=school_id,
        academic_term_id=term_id,
        class_subject_id=class_subject_id,
        name=spec["name"],
        code=code,
        assessment_type=ASSESSMENT_TYPE_BY_CODE.get(code, "CONTINUOUS_ASSESSMENT"),
        max_score=Decimal(str(spec["max_score"])),
        weight_percentage=Decimal(str(spec["weight_percentage"])),
        sequence_order=order,
    )


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
            definition = _build_assessment_definition(
                school_id, term.id, class_subject.id, code, spec, order
            )
            db.add(definition)
            created.append(definition)

    if created:
        await db.flush()
    return created


async def seed_assessment_definitions_for_term(
    db: AsyncSession,
    school_id: UUID,
    term: AcademicTerm,
) -> list[AssessmentDefinition]:
    """Seeds default AssessmentDefinition rows for all active ClassSubjects in a
    session when a new term is created (e.g., after applying a structure template).
    """
    config_stmt = select(SchoolConfiguration).where(SchoolConfiguration.school_id == school_id)
    config = (await db.execute(config_stmt)).scalar_one_or_none()
    if not config:
        return []

    class_subject_ids = (
        (
            await db.execute(
                select(ClassSubject.id)
                .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
                .where(
                    ClassSubject.school_id == school_id,
                    SubjectOffering.academic_session_id == term.academic_session_id,
                    ClassSubject.deleted_at.is_(None),
                    SubjectOffering.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not class_subject_ids:
        return []

    existing_pairs = set(
        (
            await db.execute(
                select(
                    AssessmentDefinition.class_subject_id,
                    AssessmentDefinition.code,
                ).where(
                    AssessmentDefinition.school_id == school_id,
                    AssessmentDefinition.academic_term_id == term.id,
                    AssessmentDefinition.class_subject_id.in_(class_subject_ids),
                )
            )
        ).all()
    )

    created: list[AssessmentDefinition] = []
    for class_subject_id in class_subject_ids:
        for order, (code, spec) in enumerate(config.assessment_weight_schema.items(), start=1):
            if (class_subject_id, code) in existing_pairs:
                continue
            definition = _build_assessment_definition(
                school_id, term.id, class_subject_id, code, spec, order
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
        AcademicTerm.id == academic_term_id,
        AcademicTerm.school_id == school_id,
        AcademicTerm.deleted_at.is_(None),
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
    academic_level_id: UUID | None = None,
    level_category: str | None = None,
    page: int,
    page_size: int,
) -> tuple[list[AssessmentDefinition], PageMeta]:
    scope = await get_permission_scope(db, current_user, "subjects:manage")
    approve_scope = await get_permission_scope(db, current_user, "scores:approve")
    enter_scope = await get_permission_scope(db, current_user, "scores:enter")

    if (
        not scope.has_any_access
        and not approve_scope.has_any_access
        and not enter_scope.has_any_access
    ):
        raise InsufficientPermissionException(permission_code="subjects:manage")

    stmt: Select[tuple[AssessmentDefinition]] = (
        select(AssessmentDefinition)
        .join(ClassSubject, ClassSubject.id == AssessmentDefinition.class_subject_id)
        .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
        .join(AcademicLevel, AcademicLevel.id == ClassGroup.academic_level_id)
        .where(
            AssessmentDefinition.school_id == school_id,
            ClassSubject.deleted_at.is_(None),
            ClassGroup.deleted_at.is_(None),
            AcademicLevel.deleted_at.is_(None),
        )
    )
    if academic_term_id is not None:
        stmt = stmt.where(AssessmentDefinition.academic_term_id == academic_term_id)
    if class_subject_id is not None:
        stmt = stmt.where(AssessmentDefinition.class_subject_id == class_subject_id)
    if academic_level_id is not None:
        stmt = stmt.where(ClassGroup.academic_level_id == academic_level_id)
    if level_category is not None:
        stmt = stmt.where(AcademicLevel.category == level_category)

    effective_scope = scope if scope.has_any_access else approve_scope
    if effective_scope.has_any_access:
        if not effective_scope.unscoped:
            stmt = stmt.where(
                build_scope_filter(
                    effective_scope,
                    department_column=ClassGroup.department_id,
                    academic_level_column=ClassGroup.academic_level_id,
                    class_group_column=ClassGroup.id,
                )
            )
    else:
        from app.modules.subjects.models import TeacherAssignment

        assigned_sub = select(TeacherAssignment.class_subject_id).where(
            TeacherAssignment.school_id == school_id,
            TeacherAssignment.teacher_id == current_user.id,
            TeacherAssignment.is_active.is_(True),
            TeacherAssignment.deleted_at.is_(None),
        )
        stmt = stmt.where(AssessmentDefinition.class_subject_id.in_(assigned_sub))

    stmt = stmt.order_by(
        AssessmentDefinition.sequence_order.asc(), AssessmentDefinition.created_at.asc()
    )
    if class_subject_id is None:
        scoped_stmt = stmt.add_columns(
            AcademicLevel.category,
            AcademicLevel.name,
            AcademicLevel.level_order,
        )
        rows = (await db.execute(scoped_stmt)).all()
        by_code: dict[str, AssessmentDefinition] = {}
        categories_by_code: dict[str, list[str]] = {}
        levels_by_code: dict[str, list[tuple[int, str]]] = {}
        for d, cat, lvl_name, lvl_seq in rows:
            if d.code not in by_code:
                by_code[d.code] = d
                categories_by_code[d.code] = []
                levels_by_code[d.code] = []
            if cat and cat not in categories_by_code[d.code]:
                categories_by_code[d.code].append(cat)
            if lvl_name and (lvl_seq, lvl_name) not in levels_by_code[d.code]:
                levels_by_code[d.code].append((lvl_seq, lvl_name))

        category_labels = {
            "PRIMARY": "Primary",
            "JUNIOR_SECONDARY": "Junior Secondary",
            "SENIOR_SECONDARY": "Senior Secondary",
        }
        unique_defs: list[AssessmentDefinition] = []
        for code, d in by_code.items():
            cats = categories_by_code[code]
            lvls = [name for _, name in sorted(levels_by_code[code], key=lambda x: x[0])]
            d.level_categories = cats  # type: ignore[attr-defined]
            d.level_names = lvls  # type: ignore[attr-defined]
            if len(cats) >= 3:
                d.scope_label = "All categories"  # type: ignore[attr-defined]
            elif len(cats) == 1:
                cat_label = category_labels.get(cats[0], cats[0])
                if len(lvls) == 1:
                    d.scope_label = f"{cat_label} · {lvls[0]}"  # type: ignore[attr-defined]
                elif len(lvls) <= 3:
                    d.scope_label = f"{cat_label} ({', '.join(lvls)})"  # type: ignore[attr-defined]
                else:
                    d.scope_label = f"{cat_label} ({len(lvls)} levels)"  # type: ignore[attr-defined]
            elif cats:
                d.scope_label = ", ".join(  # type: ignore[attr-defined]
                    category_labels.get(c, c) for c in cats
                )
            else:
                d.scope_label = None  # type: ignore[attr-defined]
            unique_defs.append(d)

        total = len(unique_defs)
        start = (page - 1) * page_size
        end = start + page_size
        total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 0
        return unique_defs[start:end], PageMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )

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
    term = await _get_term(db, school_id, req.academic_term_id)
    if term.is_locked_for_scoring:
        raise ValidationException(
            "This term is locked for scoring, so assessment components cannot be added."
        )
    class_subject = await _get_class_subject(db, school_id, req.class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject)
    class_group = (
        await db.execute(select(ClassGroup).where(ClassGroup.id == class_subject.class_group_id))
    ).scalar_one()
    if class_group.academic_session_id != term.academic_session_id:
        raise ValidationException(
            "The selected term does not belong to this class subject's academic session."
        )

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
    term = await _get_term(db, school_id, definition.academic_term_id)
    if term.is_locked_for_scoring:
        raise ValidationException(
            "This term is locked for scoring, so assessment components cannot be edited."
        )
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


async def delete_definition(
    db: AsyncSession,
    school_id: UUID,
    definition_id: UUID,
    current_user: User,
    audit: AuditContext,
) -> str:
    definition = await _get_definition(db, school_id, definition_id)
    term = await _get_term(db, school_id, definition.academic_term_id)
    if term.is_locked_for_scoring:
        raise ValidationException(
            "This term is locked for scoring, so assessment components cannot be removed."
        )
    class_subject = await _get_class_subject(db, school_id, definition.class_subject_id)
    await _require_class_subject_scope(db, current_user, class_subject)

    score_count = await db.execute(
        select(func.count()).where(AssessmentScore.assessment_definition_id == definition.id)
    )
    if score_count.scalar_one() > 0:
        raise ConflictException(
            message=(
                f"{definition.name} already has recorded scores and cannot be removed. "
                "You can adjust scores or change the structure in a new term."
            ),
            code="ASSESSMENT_DEFINITION_IN_USE",
        )

    removed_name = definition.name
    removed_code = definition.code
    await db.delete(definition)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ASSESSMENT_DEFINITION_DELETED",
        resource_type="AssessmentDefinition",
        resource_id=definition_id,
        diff_payload={"code": removed_code, "name": removed_name},
    )
    await db.commit()
    return f"{removed_name} has been removed."


async def _resolve_target_class_subjects(
    db: AsyncSession,
    school_id: UUID,
    current_user: User,
    *,
    session_id: UUID,
    academic_level_ids: list[UUID],
    level_category: str | None,
    apply_to_all_levels: bool,
    subject_id: UUID | None,
) -> list[ClassSubject]:
    if not apply_to_all_levels and not level_category and not academic_level_ids:
        raise ValidationException(
            "Please select a category, one or more class levels, or all levels."
        )

    scope = await get_permission_scope(db, current_user, "subjects:manage")
    if not scope.has_any_access:
        raise InsufficientPermissionException(permission_code="subjects:manage")

    stmt = (
        select(ClassSubject)
        .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
        .join(AcademicLevel, AcademicLevel.id == ClassGroup.academic_level_id)
        .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
        .where(
            ClassSubject.school_id == school_id,
            ClassSubject.deleted_at.is_(None),
            ClassGroup.deleted_at.is_(None),
            ClassGroup.academic_session_id == session_id,
            AcademicLevel.deleted_at.is_(None),
        )
    )
    if level_category:
        stmt = stmt.where(AcademicLevel.category == level_category)
    elif academic_level_ids:
        stmt = stmt.where(ClassGroup.academic_level_id.in_(academic_level_ids))
    if subject_id is not None:
        stmt = stmt.where(SubjectOffering.subject_id == subject_id)
    if not scope.unscoped:
        stmt = stmt.where(
            build_scope_filter(
                scope,
                department_column=ClassGroup.department_id,
                academic_level_column=ClassGroup.academic_level_id,
                class_group_column=ClassGroup.id,
            )
        )
    return list((await db.execute(stmt)).scalars().all())


async def bulk_create_definitions(
    db: AsyncSession,
    school_id: UUID,
    req: AssessmentDefinitionBulkCreateRequest,
    current_user: User,
    audit: AuditContext,
) -> AssessmentDefinitionBulkActionResponse:
    term = await _get_term(db, school_id, req.academic_term_id)
    if term.is_locked_for_scoring:
        raise ValidationException(
            "This term is locked for scoring, so assessment components cannot be added."
        )

    targets = await _resolve_target_class_subjects(
        db,
        school_id,
        current_user,
        session_id=term.academic_session_id,
        academic_level_ids=req.academic_level_ids,
        level_category=req.level_category,
        apply_to_all_levels=req.apply_to_all_levels,
        subject_id=req.subject_id,
    )
    if not targets:
        raise ValidationException(
            "No matching class subjects were found for the selected class levels or category."
        )

    code = req.code.strip().upper()
    target_ids = [cs.id for cs in targets]
    existing_cs_ids = set(
        (
            await db.execute(
                select(AssessmentDefinition.class_subject_id).where(
                    AssessmentDefinition.school_id == school_id,
                    AssessmentDefinition.academic_term_id == req.academic_term_id,
                    AssessmentDefinition.class_subject_id.in_(target_ids),
                    AssessmentDefinition.code == code,
                )
            )
        )
        .scalars()
        .all()
    )

    affected_count = 0
    skipped_count = 0
    for cs in targets:
        if cs.id in existing_cs_ids:
            skipped_count += 1
            continue
        db.add(
            AssessmentDefinition(
                school_id=school_id,
                academic_term_id=req.academic_term_id,
                class_subject_id=cs.id,
                name=req.name.strip(),
                code=code,
                assessment_type=req.assessment_type.value,
                max_score=req.max_score,
                weight_percentage=req.weight_percentage,
                sequence_order=req.sequence_order,
            )
        )
        affected_count += 1

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ASSESSMENT_DEFINITION_BULK_CREATED",
        resource_type="AssessmentDefinition",
        resource_id=req.academic_term_id,
        diff_payload={
            "code": code,
            "name": req.name.strip(),
            "affected_count": affected_count,
            "skipped_count": skipped_count,
        },
    )
    await _commit_or_conflict(
        db,
        message="Some assessment components could not be created. Please try again.",
        code="ASSESSMENT_DEFINITION_CONFLICT",
    )

    if affected_count > 0:
        message = (
            f"Added {req.name.strip()} to {affected_count} class "
            f"{'subject' if affected_count == 1 else 'subjects'}."
        )
        if skipped_count > 0:
            message += f" {skipped_count} already had this component."
    else:
        message = f"All {skipped_count} matching class subjects already have {req.name.strip()}."

    return AssessmentDefinitionBulkActionResponse(
        affected_count=affected_count,
        skipped_count=skipped_count,
        message=message,
    )


async def bulk_remove_definitions(
    db: AsyncSession,
    school_id: UUID,
    req: AssessmentDefinitionBulkRemoveRequest,
    current_user: User,
    audit: AuditContext,
) -> AssessmentDefinitionBulkActionResponse:
    term = await _get_term(db, school_id, req.academic_term_id)
    if term.is_locked_for_scoring:
        raise ValidationException(
            "This term is locked for scoring, so assessment components cannot be removed."
        )

    targets = await _resolve_target_class_subjects(
        db,
        school_id,
        current_user,
        session_id=term.academic_session_id,
        academic_level_ids=req.academic_level_ids,
        level_category=req.level_category,
        apply_to_all_levels=req.apply_to_all_levels,
        subject_id=req.subject_id,
    )
    if not targets:
        return AssessmentDefinitionBulkActionResponse(
            affected_count=0,
            skipped_count=0,
            message="No matching class subjects were found for the selected scope.",
        )

    code = req.code.strip().upper()
    target_ids = [cs.id for cs in targets]
    defs = (
        (
            await db.execute(
                select(AssessmentDefinition).where(
                    AssessmentDefinition.school_id == school_id,
                    AssessmentDefinition.academic_term_id == req.academic_term_id,
                    AssessmentDefinition.class_subject_id.in_(target_ids),
                    AssessmentDefinition.code == code,
                )
            )
        )
        .scalars()
        .all()
    )
    if not defs:
        return AssessmentDefinitionBulkActionResponse(
            affected_count=0,
            skipped_count=0,
            message="No matching assessment components were found to remove.",
        )

    def_ids = [d.id for d in defs]
    in_use_def_ids = set(
        (
            await db.execute(
                select(AssessmentScore.assessment_definition_id)
                .where(AssessmentScore.assessment_definition_id.in_(def_ids))
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    affected_count = 0
    skipped_count = 0
    for d in defs:
        if d.id in in_use_def_ids:
            skipped_count += 1
            continue
        await db.delete(d)
        affected_count += 1

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ASSESSMENT_DEFINITION_BULK_DELETED",
        resource_type="AssessmentDefinition",
        resource_id=req.academic_term_id,
        diff_payload={
            "code": code,
            "affected_count": affected_count,
            "skipped_count": skipped_count,
        },
    )
    await db.commit()

    if affected_count > 0:
        message = (
            f"Removed {code} from {affected_count} class "
            f"{'subject' if affected_count == 1 else 'subjects'}."
        )
        if skipped_count > 0:
            message += f" {skipped_count} with recorded scores were kept."
    else:
        message = (
            f"No components were removed because all {skipped_count} matching class subjects "
            "already have recorded scores."
        )

    return AssessmentDefinitionBulkActionResponse(
        affected_count=affected_count,
        skipped_count=skipped_count,
        message=message,
    )


async def bulk_update_definitions(
    db: AsyncSession,
    school_id: UUID,
    req: AssessmentDefinitionBulkUpdateRequest,
    current_user: User,
    audit: AuditContext,
) -> AssessmentDefinitionBulkActionResponse:
    term = await _get_term(db, school_id, req.academic_term_id)
    if term.is_locked_for_scoring:
        raise ValidationException(
            "This term is locked for scoring, so assessment components cannot be edited."
        )

    targets = await _resolve_target_class_subjects(
        db,
        school_id,
        current_user,
        session_id=term.academic_session_id,
        academic_level_ids=req.academic_level_ids,
        level_category=req.level_category,
        apply_to_all_levels=req.apply_to_all_levels,
        subject_id=None,
    )
    if not targets:
        return AssessmentDefinitionBulkActionResponse(
            affected_count=0,
            skipped_count=0,
            message="No matching class subjects were found for the selected scope.",
        )

    code = req.code.strip().upper()
    target_ids = [cs.id for cs in targets]
    defs = (
        (
            await db.execute(
                select(AssessmentDefinition).where(
                    AssessmentDefinition.school_id == school_id,
                    AssessmentDefinition.academic_term_id == req.academic_term_id,
                    AssessmentDefinition.class_subject_id.in_(target_ids),
                    AssessmentDefinition.code == code,
                )
            )
        )
        .scalars()
        .all()
    )
    if not defs:
        return AssessmentDefinitionBulkActionResponse(
            affected_count=0,
            skipped_count=0,
            message="No matching assessment components were found to update.",
        )

    def_ids = [d.id for d in defs]
    in_use_def_ids = set(
        (
            await db.execute(
                select(AssessmentScore.assessment_definition_id)
                .where(AssessmentScore.assessment_definition_id.in_(def_ids))
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    affected_count = 0
    skipped_count = 0
    for d in defs:
        if d.id in in_use_def_ids:
            skipped_count += 1
            continue
        if req.name is not None:
            d.name = req.name.strip()
        if req.max_score is not None:
            d.max_score = req.max_score
        if req.weight_percentage is not None:
            d.weight_percentage = req.weight_percentage
        if req.sequence_order is not None:
            d.sequence_order = req.sequence_order
        affected_count += 1

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ASSESSMENT_DEFINITION_BULK_UPDATED",
        resource_type="AssessmentDefinition",
        resource_id=req.academic_term_id,
        diff_payload={
            "code": code,
            "affected_count": affected_count,
            "skipped_count": skipped_count,
        },
    )
    await _commit_or_conflict(
        db,
        message="Some assessment components could not be updated. Please try again.",
        code="ASSESSMENT_DEFINITION_CONFLICT",
    )

    if affected_count > 0:
        message = (
            f"Updated {code} across {affected_count} class "
            f"{'subject' if affected_count == 1 else 'subjects'}."
        )
        if skipped_count > 0:
            message += f" {skipped_count} with recorded scores were kept unchanged."
    else:
        message = (
            f"No components were updated because all {skipped_count} matching class subjects "
            "already have recorded scores."
        )

    return AssessmentDefinitionBulkActionResponse(
        affected_count=affected_count,
        skipped_count=skipped_count,
        message=message,
    )


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
            StudentEnrollment.deleted_at.is_(None),
            Student.deleted_at.is_(None),
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
    term = await _get_term(db, school_id, definition.academic_term_id)
    if term.is_locked_for_scoring:
        raise ValidationException("This term is locked for scoring, so scores cannot be edited.")
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

    deduped_scores: dict[UUID, Any] = {}
    for item in req.scores:
        deduped_scores[item.student_enrollment_id] = item
    unique_scores = list(deduped_scores.values())

    registered_stmt = select(StudentSubjectRegistration.student_enrollment_id).where(
        StudentSubjectRegistration.class_subject_id == class_subject.id,
        StudentSubjectRegistration.is_active.is_(True),
    )
    registered_ids = set((await db.execute(registered_stmt)).scalars().all())

    target_enrollment_ids = [
        item.student_enrollment_id
        for item in unique_scores
        if item.student_enrollment_id in registered_ids
        and not (item.raw_score is not None and item.raw_score > definition.max_score)
    ]
    existing_scores_by_enrollment: dict[UUID, AssessmentScore] = {}
    if target_enrollment_ids:
        existing_stmt = select(AssessmentScore).where(
            AssessmentScore.assessment_definition_id == definition.id,
            AssessmentScore.student_enrollment_id.in_(target_enrollment_ids),
        )
        existing_scores_by_enrollment = {
            sc.student_enrollment_id: sc for sc in (await db.execute(existing_stmt)).scalars().all()
        }

    errors: list[ScoreEntryError] = []
    updated_count = 0
    for item in unique_scores:
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

        existing = existing_scores_by_enrollment.get(item.student_enrollment_id)
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


async def list_score_sheets_overview(
    db: AsyncSession,
    school_id: UUID,
    academic_term_id: UUID,
    current_user: User,
    *,
    class_group_id: UUID | None = None,
    subject_id: UUID | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    from app.modules.subjects.models import (
        ClassSubject,
        Subject,
        SubjectOffering,
        TeacherAssignment,
    )

    term = (
        await db.execute(
            select(AcademicTerm).where(
                AcademicTerm.id == academic_term_id,
                AcademicTerm.school_id == school_id,
                AcademicTerm.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if term is None:
        raise ResourceNotFoundException("academic term", academic_term_id)

    approve_scope = await get_permission_scope(db, current_user, "scores:approve")
    enter_scope = await get_permission_scope(db, current_user, "scores:enter")

    stmt = (
        select(
            ClassSubject.id,
            ClassGroup.id,
            ClassGroup.display_name,
            Subject.id,
            Subject.name,
            Subject.code,
        )
        .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
        .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
        .join(Subject, Subject.id == SubjectOffering.subject_id)
        .where(
            ClassSubject.school_id == school_id,
            ClassSubject.deleted_at.is_(None),
            ClassGroup.academic_session_id == term.academic_session_id,
            ClassGroup.deleted_at.is_(None),
        )
    )

    if not approve_scope.has_any_access and not enter_scope.has_any_access:
        raise InsufficientPermissionException(permission_code="scores:enter")

    from sqlalchemy import or_

    access_clauses = []
    if approve_scope.has_any_access:
        if approve_scope.unscoped:
            access_clauses = []
        else:
            access_clauses.append(
                build_scope_filter(
                    approve_scope,
                    department_column=ClassGroup.department_id,
                    academic_level_column=ClassGroup.academic_level_id,
                    class_group_column=ClassGroup.id,
                )
            )
    if enter_scope.has_any_access and not (approve_scope.has_any_access and approve_scope.unscoped):
        if enter_scope.unscoped and current_user.role != "TEACHER":
            access_clauses = []
        else:
            assigned_sub = select(TeacherAssignment.class_subject_id).where(
                TeacherAssignment.school_id == school_id,
                TeacherAssignment.teacher_id == current_user.id,
                TeacherAssignment.is_active.is_(True),
                TeacherAssignment.deleted_at.is_(None),
            )
            access_clauses.append(ClassSubject.id.in_(assigned_sub))

    if access_clauses:
        stmt = stmt.where(or_(*access_clauses))

    if class_group_id is not None:
        stmt = stmt.where(ClassGroup.id == class_group_id)
    if subject_id is not None:
        stmt = stmt.where(Subject.id == subject_id)

    all_cs = (
        await db.execute(stmt.order_by(ClassGroup.display_name.asc(), Subject.name.asc()))
    ).all()
    cs_ids = [row[0] for row in all_cs]

    status_map: dict[UUID, ScoreSheetStatus] = {}
    teacher_map: dict[UUID, list[str]] = {}
    if cs_ids:
        status_rows = (
            (
                await db.execute(
                    select(ScoreSheetStatus).where(
                        ScoreSheetStatus.school_id == school_id,
                        ScoreSheetStatus.academic_term_id == term.id,
                        ScoreSheetStatus.class_subject_id.in_(cs_ids),
                        ScoreSheetStatus.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        status_map = {s.class_subject_id: s for s in status_rows}

        ta_rows = (
            await db.execute(
                select(TeacherAssignment.class_subject_id, User.first_name, User.last_name)
                .join(User, User.id == TeacherAssignment.teacher_id)
                .where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.class_subject_id.in_(cs_ids),
                    TeacherAssignment.is_active.is_(True),
                    TeacherAssignment.deleted_at.is_(None),
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        ).all()
        for cs_id, fname, lname in ta_rows:
            teacher_map.setdefault(cs_id, []).append(f"{fname} {lname}".strip())

    assembled: list[dict[str, Any]] = []
    for cs_id, cg_id, cg_name, s_id, s_name, s_code in all_cs:
        st_row = status_map.get(cs_id)
        st_val = st_row.status if st_row else "DRAFT"
        assembled.append(
            {
                "class_subject_id": cs_id,
                "academic_term_id": term.id,
                "class_group_id": cg_id,
                "class_group_name": cg_name,
                "subject_id": s_id,
                "subject_name": s_name,
                "subject_code": s_code,
                "teacher_names": teacher_map.get(cs_id, []),
                "status": st_val,
                "submitted_at": st_row.submitted_at if st_row else None,
                "approved_at": st_row.approved_at if st_row else None,
                "unlocked_at": st_row.unlocked_at if st_row else None,
                "unlock_reason": st_row.unlock_reason if st_row else None,
            }
        )

    summary = {
        "total": len(assembled),
        "draft": sum(1 for item in assembled if item["status"] == "DRAFT"),
        "submitted": sum(1 for item in assembled if item["status"] == "SUBMITTED"),
        "approved": sum(1 for item in assembled if item["status"] == "APPROVED"),
    }

    if status_filter:
        normalized = status_filter.strip().upper()
        assembled = [item for item in assembled if item["status"] == normalized]

    total = len(assembled)
    offset = (page - 1) * page_size
    paged = assembled[offset : offset + page_size]
    meta = PageMeta.build(page=page, page_size=page_size, total=total)
    return paged, meta, summary


async def export_mark_book(
    db: AsyncSession,
    school_id: UUID,
    current_user: User,
    *,
    class_subject_id: UUID | None = None,
    all_arms: bool = False,
    class_group_id: UUID | None = None,
    academic_session_id: UUID | None = None,
    subject_id: UUID | None = None,
    academic_level_id: UUID | None = None,
) -> tuple[bytes, str, str]:
    XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ZIP_MIME = "application/zip"

    if (
        class_subject_id is None
        and class_group_id is None
        and (academic_session_id is None or (subject_id is None and academic_level_id is None))
    ):
        raise ValidationException(
            "Provide a class subject, a class group, or an academic session with a subject or level to export the mark book."
        )

    school = (await db.execute(select(School).where(School.id == school_id))).scalar_one_or_none()
    if not school:
        raise ResourceNotFoundException("school", school_id)

    config = (
        await db.execute(
            select(SchoolConfiguration).where(SchoolConfiguration.school_id == school_id)
        )
    ).scalar_one_or_none()
    grading_schema = config.grading_scale_schema if config else {"grades": []}

    # Expand class_subject_id + all_arms=true into (academic_session_id, academic_level_id, subject_id)
    if class_subject_id is not None and all_arms:
        anchor = (
            await db.execute(
                select(
                    ClassGroup.academic_session_id,
                    ClassGroup.academic_level_id,
                    SubjectOffering.subject_id,
                )
                .select_from(ClassSubject)
                .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
                .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
                .where(
                    ClassSubject.id == class_subject_id,
                    ClassSubject.school_id == school_id,
                    ClassSubject.deleted_at.is_(None),
                )
            )
        ).first()
        if not anchor:
            raise ResourceNotFoundException("class subject", class_subject_id)
        academic_session_id, academic_level_id, subject_id = anchor
        class_subject_id = None

    cs_stmt = (
        select(ClassSubject, ClassGroup, AcademicLevel, Subject, SubjectOffering)
        .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
        .join(AcademicLevel, AcademicLevel.id == ClassGroup.academic_level_id)
        .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
        .join(Subject, Subject.id == SubjectOffering.subject_id)
        .where(
            ClassSubject.school_id == school_id,
            ClassSubject.deleted_at.is_(None),
            ClassGroup.deleted_at.is_(None),
        )
        .order_by(AcademicLevel.level_order, Subject.name, ClassGroup.display_name)
    )
    if class_subject_id is not None:
        cs_stmt = cs_stmt.where(ClassSubject.id == class_subject_id)
    elif class_group_id is not None:
        cs_stmt = cs_stmt.where(ClassGroup.id == class_group_id)
    else:
        cs_stmt = cs_stmt.where(ClassGroup.academic_session_id == academic_session_id)
        if subject_id is not None:
            cs_stmt = cs_stmt.where(SubjectOffering.subject_id == subject_id)
        if academic_level_id is not None:
            cs_stmt = cs_stmt.where(ClassGroup.academic_level_id == academic_level_id)

    cs_rows = (await db.execute(cs_stmt)).all()
    if not cs_rows:
        raise ResourceNotFoundException(
            "class subject",
            class_subject_id or class_group_id or subject_id or academic_level_id or school_id,
        )

    # Filter or authorize access per class subject (allowing assigned Form Master on class_group_id)
    authorized_rows = []
    for row in cs_rows:
        cs, cg, _, _, _ = row
        if current_user.role == "TEACHER" and cg.class_teacher_id == current_user.id:
            authorized_rows.append(row)
            continue
        try:
            await _check_class_subject_access(db, school_id, cs, current_user, "scores:enter")
            authorized_rows.append(row)
        except InsufficientPermissionException:
            # For multi-arm exports by a teacher, include only the arms they teach
            if (
                current_user.role == "TEACHER"
                and class_subject_id is None
                and class_group_id is None
            ):
                continue
            raise

    if not authorized_rows:
        raise InsufficientPermissionException(permission_code="scores:enter")
    cs_rows = authorized_rows

    session_id = cs_rows[0][1].academic_session_id
    session = (
        await db.execute(
            select(AcademicSession).where(
                AcademicSession.id == session_id, AcademicSession.school_id == school_id
            )
        )
    ).scalar_one()

    terms = (
        (
            await db.execute(
                select(AcademicTerm)
                .where(
                    AcademicTerm.school_id == school_id,
                    AcademicTerm.academic_session_id == session_id,
                    AcademicTerm.deleted_at.is_(None),
                )
                .order_by(AcademicTerm.term_order)
            )
        )
        .scalars()
        .all()
    )
    term_order_by_id = {term.id: term.term_order for term in terms}

    cs_ids = [cs.id for cs, _, _, _, _ in cs_rows]

    teacher_rows = (
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
    teachers_by_cs: dict[UUID, list[str]] = {}
    for cs_id_val, fname, lname in teacher_rows:
        teachers_by_cs.setdefault(cs_id_val, []).append(f"{fname} {lname}".strip())

    defs_rows = (
        (
            await db.execute(
                select(AssessmentDefinition).where(
                    AssessmentDefinition.school_id == school_id,
                    AssessmentDefinition.class_subject_id.in_(cs_ids),
                    AssessmentDefinition.academic_term_id.in_(list(term_order_by_id.keys())),
                )
            )
        )
        .scalars()
        .all()
    )
    defs_by_id = {d.id: d for d in defs_rows}

    reg_rows = (
        await db.execute(
            select(
                StudentSubjectRegistration.class_subject_id,
                StudentEnrollment.id,
                Student.first_name,
                Student.last_name,
            )
            .join(
                StudentEnrollment,
                StudentEnrollment.id == StudentSubjectRegistration.student_enrollment_id,
            )
            .join(Student, Student.id == StudentEnrollment.student_id)
            .where(
                StudentSubjectRegistration.school_id == school_id,
                StudentSubjectRegistration.class_subject_id.in_(cs_ids),
                StudentSubjectRegistration.is_active.is_(True),
                StudentEnrollment.deleted_at.is_(None),
                Student.deleted_at.is_(None),
            )
            .order_by(Student.last_name, Student.first_name)
        )
    ).all()

    enrollment_ids = list({row[1] for row in reg_rows})
    scores_by_key: dict[tuple[UUID, UUID], Decimal | None] = {}
    if enrollment_ids and defs_by_id:
        score_rows = (
            (
                await db.execute(
                    select(AssessmentScore).where(
                        AssessmentScore.school_id == school_id,
                        AssessmentScore.student_enrollment_id.in_(enrollment_ids),
                        AssessmentScore.assessment_definition_id.in_(list(defs_by_id.keys())),
                    )
                )
            )
            .scalars()
            .all()
        )
        for sc in score_rows:
            if sc.score_status == "SCORED" and sc.raw_score is not None:
                scores_by_key[(sc.assessment_definition_id, sc.student_enrollment_id)] = (
                    sc.raw_score
                )

    students_by_cs: dict[UUID, list[tuple[UUID, str]]] = {}
    for cs_id_val, enr_id, fname, lname in reg_rows:
        students_by_cs.setdefault(cs_id_val, []).append((enr_id, f"{lname} {fname}".strip()))

    defs_by_cs: dict[UUID, list[AssessmentDefinition]] = {}
    for defn in defs_rows:
        defs_by_cs.setdefault(defn.class_subject_id, []).append(defn)

    safe_session = session.name.replace("/", "-")

    def _build_sheet(row_tuple: tuple[Any, ...], sheet_title: str) -> mark_book.MarkBookSheetData:
        cs, cg, level, subject, _ = row_tuple
        resolved_scale = engine.resolve_grading_scale(grading_schema, level.category)
        grades_list = (
            list(resolved_scale.get("grades", [])) if isinstance(resolved_scale, dict) else []
        )
        teacher_name = ", ".join(teachers_by_cs.get(cs.id, [])) or "UNASSIGNED"
        cs_defs = defs_by_cs.get(cs.id, [])

        student_rows: list[mark_book.MarkBookStudentRow] = []
        for enr_id, full_name in students_by_cs.get(cs.id, []):
            score_map: dict[tuple[int, str], Decimal | None] = {}
            for defn in cs_defs:
                t_order = term_order_by_id.get(defn.academic_term_id)
                if t_order in (1, 2, 3):
                    raw = scores_by_key.get((defn.id, enr_id))
                    if raw is not None:
                        score_map[(t_order, defn.code.upper())] = raw
            student_rows.append(mark_book.MarkBookStudentRow(full_name=full_name, scores=score_map))

        return mark_book.MarkBookSheetData(
            sheet_title=sheet_title,
            school_name=school.name,
            session_name=session.name,
            subject_name=subject.name,
            class_name=cg.display_name,
            teacher_name=teacher_name,
            grading_grades=grades_list,
            students=student_rows,
        )

    # Case 1: Level-wide export without subject_id -> ZIP of multi-arm .xlsx per subject
    if class_subject_id is None and class_group_id is None and subject_id is None:
        by_subject: dict[UUID, list[tuple[Any, ...]]] = {}
        for row in cs_rows:
            by_subject.setdefault(row[3].id, []).append(row)

        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for subj_rows in by_subject.values():
                subj_code = subj_rows[0][3].code.replace(" ", "_")
                subj_sheets = [_build_sheet(r, r[1].display_name) for r in subj_rows]
                zf.writestr(
                    f"MARK_BOOK_{subj_code}_{safe_session}.xlsx",
                    mark_book.build_mark_book_xlsx(subj_sheets),
                )
        level_code = cs_rows[0][2].code.replace(" ", "_")
        return zip_buf.getvalue(), f"MARK_BOOKS_{level_code}_{safe_session}.zip", ZIP_MIME

    # Case 2: Class arm export (class_group_id) -> 1 workbook with 1 tab per subject
    if class_group_id is not None:
        sheets = [_build_sheet(r, f"{r[3].code} - {r[3].name}") for r in cs_rows]
        safe_cg = cs_rows[0][1].display_name.replace(" ", "_")
        return (
            mark_book.build_mark_book_xlsx(sheets),
            f"MARK_BOOK_{safe_cg}_{safe_session}.xlsx",
            XLSX_MIME,
        )

    # Case 3: Single class_subject_id or multi-arm subject workbook
    sheets = [_build_sheet(r, r[1].display_name) for r in cs_rows]
    safe_subject = cs_rows[0][3].code.replace(" ", "_")
    return (
        mark_book.build_mark_book_xlsx(sheets),
        f"MARK_BOOK_{safe_subject}_{safe_session}.xlsx",
        XLSX_MIME,
    )
