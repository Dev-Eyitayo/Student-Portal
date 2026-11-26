from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    InsufficientPermissionException,
    ResourceNotFoundException,
    ValidationException,
)
from app.core.permissions import user_has_permission
from app.modules.academic_structure.models import (
    AcademicLevel,
    AcademicTerm,
    ClassGroup,
    Department,
)
from app.modules.assessments.models import AssessmentDefinition, AssessmentScore, ScoreSheetStatus
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.common.schemas import PageMeta
from app.modules.computation import broadsheet_export, engine
from app.modules.computation.models import SubjectResult, TermReportCard
from app.modules.computation.schemas import (
    BroadsheetResponse,
    CalculateTermRequest,
    CalculateTermResponse,
    ComponentScoreResponse,
    PromotionDecision,
    StudentBroadsheetRow,
    SubjectComputationResponse,
)
from app.modules.identity.models import School, SchoolConfiguration, User
from app.modules.students.models import Student, StudentEnrollment
from app.modules.subjects.models import (
    ClassSubject,
    StudentSubjectRegistration,
    Subject,
    SubjectOffering,
)

TERM_KEYS = {1: "first_term", 2: "second_term", 3: "third_term"}


async def _commit_or_conflict(
    db: AsyncSession,
    *,
    message: str,
    code: str = "COMPUTATION_CONFLICT",
) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictException(message=message, code=code) from exc


async def get_term(db: AsyncSession, school_id: UUID, term_id: UUID) -> AcademicTerm:
    stmt = select(AcademicTerm).where(
        AcademicTerm.id == term_id,
        AcademicTerm.school_id == school_id,
        AcademicTerm.deleted_at.is_(None),
    )
    term = (await db.execute(stmt)).scalar_one_or_none()
    if not term:
        raise ResourceNotFoundException("academic term", term_id)
    return term


async def _get_config(db: AsyncSession, school_id: UUID) -> SchoolConfiguration:
    stmt = select(SchoolConfiguration).where(SchoolConfiguration.school_id == school_id)
    config = (await db.execute(stmt)).scalar_one_or_none()
    if not config:
        raise ResourceNotFoundException("school configuration", school_id)
    return config


def build_snapshot(config: SchoolConfiguration) -> dict[str, object]:
    return {
        "engine_version": "m5_v2",
        "grading_scale_schema": config.grading_scale_schema,
        "assessment_weight_schema": config.assessment_weight_schema,
        "academic_term_definitions": config.academic_term_definitions,
        "annual_results_schema": config.annual_results_schema,
        "report_card_template_id": config.report_card_template_id,
        "show_department_position_on_report_card": config.show_department_position_on_report_card,
        "show_level_position_on_report_card": config.show_level_position_on_report_card,
        "show_subject_position_on_report_card": config.show_subject_position_on_report_card,
    }


async def _get_class_subject_context(
    db: AsyncSession,
    school_id: UUID,
    term: AcademicTerm,
    class_group_id: UUID | None,
    *,
    class_group_ids: list[UUID] | None = None,
) -> list[tuple[ClassSubject, ClassGroup, Subject]]:
    stmt: Select[tuple[ClassSubject, ClassGroup, Subject]] = (
        select(ClassSubject, ClassGroup, Subject)
        .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
        .join(Subject, Subject.id == SubjectOffering.subject_id)
        .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
        .where(
            ClassSubject.school_id == school_id,
            ClassSubject.deleted_at.is_(None),
            SubjectOffering.academic_session_id == term.academic_session_id,
        )
    )
    if class_group_id is not None:
        stmt = stmt.where(ClassSubject.class_group_id == class_group_id)
    elif class_group_ids is not None:
        if not class_group_ids:
            return []
        stmt = stmt.where(ClassSubject.class_group_id.in_(class_group_ids))
    stmt = stmt.where(
        ClassGroup.deleted_at.is_(None),
        SubjectOffering.deleted_at.is_(None),
    )
    return list((await db.execute(stmt)).all())


async def _require_computation_access(
    db: AsyncSession,
    current_user: User,
    class_groups: list[ClassGroup],
    *,
    allow_form_master: bool = False,
) -> None:
    if await user_has_permission(db, current_user, "scores:approve"):
        return
    for class_group in class_groups:
        if allow_form_master and current_user.id == class_group.class_teacher_id:
            continue
        allowed = await user_has_permission(
            db,
            current_user,
            "scores:approve",
            department_id=class_group.department_id,
            academic_level_id=class_group.academic_level_id,
            class_group_id=class_group.id,
        )
        if not allowed:
            raise InsufficientPermissionException(permission_code="scores:approve")


async def _load_computation_inputs(
    db: AsyncSession,
    school_id: UUID,
    term: AcademicTerm,
    context: list[tuple[ClassSubject, ClassGroup, Subject]],
    *,
    require_approved: bool = True,
) -> tuple[
    dict[str, list[engine.SubjectInput]],
    dict[str, tuple[Student, StudentEnrollment]],
    dict[str, tuple[str, str, str]],
    dict[str, str],
    dict[str, str],
]:
    class_subject_ids = [class_subject.id for class_subject, _, _ in context]
    if not class_subject_ids:
        return {}, {}, {}, {}, {}

    if require_approved:
        status_stmt = select(ScoreSheetStatus).where(
            ScoreSheetStatus.school_id == school_id,
            ScoreSheetStatus.academic_term_id == term.id,
            ScoreSheetStatus.class_subject_id.in_(class_subject_ids),
        )
        statuses = {
            status.class_subject_id: status.status
            for status in (await db.execute(status_stmt)).scalars().all()
        }
        not_approved = [
            class_subject_id
            for class_subject_id in class_subject_ids
            if statuses.get(class_subject_id) != "APPROVED"
        ]
        if not_approved:
            raise ValidationException(
                f"Results cannot be calculated yet because {len(not_approved)} score sheet(s) "
                "are not approved. Please approve the outstanding score sheets first."
            )

    definitions_stmt = select(AssessmentDefinition).where(
        AssessmentDefinition.school_id == school_id,
        AssessmentDefinition.academic_term_id == term.id,
        AssessmentDefinition.class_subject_id.in_(class_subject_ids),
    )
    definitions = (await db.execute(definitions_stmt)).scalars().all()
    definitions_by_class_subject: dict[UUID, list[AssessmentDefinition]] = {}
    for definition in definitions:
        definitions_by_class_subject.setdefault(definition.class_subject_id, []).append(definition)

    registrations_stmt = select(StudentSubjectRegistration).where(
        StudentSubjectRegistration.school_id == school_id,
        StudentSubjectRegistration.class_subject_id.in_(class_subject_ids),
        StudentSubjectRegistration.is_active.is_(True),
    )
    registrations = (await db.execute(registrations_stmt)).scalars().all()
    enrollment_ids = {registration.student_enrollment_id for registration in registrations}
    if not enrollment_ids:
        return {}, {}, {}, {}, {}

    enrollment_stmt = select(StudentEnrollment).where(
        StudentEnrollment.school_id == school_id,
        StudentEnrollment.id.in_(enrollment_ids),
        StudentEnrollment.deleted_at.is_(None),
    )
    enrollments = {
        enrollment.id: enrollment
        for enrollment in (await db.execute(enrollment_stmt)).scalars().all()
    }
    student_stmt = select(Student).where(
        Student.school_id == school_id,
        Student.id.in_({enrollment.student_id for enrollment in enrollments.values()}),
        Student.deleted_at.is_(None),
    )
    students = {student.id: student for student in (await db.execute(student_stmt)).scalars().all()}

    scores_stmt = select(AssessmentScore).where(
        AssessmentScore.school_id == school_id,
        AssessmentScore.student_enrollment_id.in_(list(enrollments)),
        AssessmentScore.assessment_definition_id.in_([definition.id for definition in definitions]),
    )
    scores = {
        (score.assessment_definition_id, score.student_enrollment_id): score
        for score in (await db.execute(scores_stmt)).scalars().all()
    }

    subject_metadata = {
        str(class_subject.id): (class_group.display_name, subject.code, subject.name)
        for class_subject, class_group, subject in context
    }
    student_metadata = {
        str(enrollment.id): (students[enrollment.student_id], enrollment)
        for enrollment in enrollments.values()
        if enrollment.student_id in students
    }

    subject_by_class_subject_id = {
        class_subject.id: subject for class_subject, _, subject in context
    }
    class_group_by_cs_id = {
        class_subject.id: class_group for class_subject, class_group, _ in context
    }
    students_inputs: dict[str, list[engine.SubjectInput]] = {}
    student_dept_cohorts: dict[str, str] = {}
    student_level_cohorts: dict[str, str] = {}
    for registration in registrations:
        class_subject_id = registration.class_subject_id
        enrollment_id = registration.student_enrollment_id
        if (
            class_subject_id not in definitions_by_class_subject
            or str(enrollment_id) not in student_metadata
        ):
            continue
        subject = subject_by_class_subject_id[class_subject_id]
        cg = class_group_by_cs_id[class_subject_id]
        student_dept_cohorts[str(enrollment_id)] = (
            f"{cg.academic_level_id}:{cg.department_id or 'GENERAL'}"
        )
        student_level_cohorts[str(enrollment_id)] = str(cg.academic_level_id)
        components: list[engine.AssessmentInput] = []
        for definition in sorted(
            definitions_by_class_subject[class_subject_id], key=lambda item: item.sequence_order
        ):
            score = scores.get((definition.id, enrollment_id))
            components.append(
                engine.AssessmentInput(
                    code=definition.code,
                    max_score=definition.max_score,
                    weight_percentage=definition.weight_percentage,
                    score_status=score.score_status if score else "PENDING",
                    raw_score=score.raw_score if score else None,
                )
            )
        students_inputs.setdefault(str(enrollment_id), []).append(
            engine.SubjectInput(
                class_subject_id=str(class_subject_id),
                subject_code=subject.code,
                subject_name=subject.name,
                assessments=tuple(components),
                subject_rank_key=f"{cg.academic_level_id}:{subject.id}",
            )
        )

    return (
        students_inputs,
        student_metadata,
        subject_metadata,
        student_dept_cohorts,
        student_level_cohorts,
    )


async def _get_existing_subject_results(
    db: AsyncSession,
    school_id: UUID,
    term_id: UUID,
    *,
    enrollment_ids: list[UUID] | None = None,
) -> dict[tuple[UUID, UUID], SubjectResult]:
    stmt = select(SubjectResult).where(
        SubjectResult.school_id == school_id, SubjectResult.academic_term_id == term_id
    )
    if enrollment_ids is not None:
        if not enrollment_ids:
            return {}
        stmt = stmt.where(SubjectResult.student_enrollment_id.in_(enrollment_ids))
    return {
        (result.student_enrollment_id, result.class_subject_id): result
        for result in (await db.execute(stmt)).scalars().all()
    }


async def _get_existing_report_cards(
    db: AsyncSession,
    school_id: UUID,
    term_id: UUID,
    *,
    enrollment_ids: list[UUID] | None = None,
) -> dict[UUID, TermReportCard]:
    stmt = select(TermReportCard).where(
        TermReportCard.school_id == school_id, TermReportCard.academic_term_id == term_id
    )
    if enrollment_ids is not None:
        if not enrollment_ids:
            return {}
        stmt = stmt.where(TermReportCard.student_enrollment_id.in_(enrollment_ids))
    return {card.student_enrollment_id: card for card in (await db.execute(stmt)).scalars().all()}


async def _load_student_categories(
    db: AsyncSession,
    school_id: UUID,
    context: list[tuple[ClassSubject, ClassGroup, Subject]],
    student_metadata: dict[str, tuple[Student, StudentEnrollment]],
) -> dict[str, str]:
    level_by_cg_id = {
        class_group.id: class_group.academic_level_id for _, class_group, _ in context
    }
    level_ids = set(level_by_cg_id.values())
    if not level_ids:
        return {}
    levels_stmt = select(AcademicLevel.id, AcademicLevel.category).where(
        AcademicLevel.school_id == school_id,
        AcademicLevel.id.in_(level_ids),
    )
    category_by_level_id = {row.id: row.category for row in (await db.execute(levels_stmt)).all()}
    return {
        student_id: category_by_level_id[level_by_cg_id[enrollment.class_group_id]]
        for student_id, (_, enrollment) in student_metadata.items()
        if enrollment.class_group_id in level_by_cg_id
        and level_by_cg_id[enrollment.class_group_id] in category_by_level_id
    }


async def _apply_annual_data(
    db: AsyncSession,
    school_id: UUID,
    term: AcademicTerm,
    calculation: engine.TermComputation,
    subject_results: dict[tuple[UUID, UUID], SubjectResult],
    report_cards: dict[UUID, TermReportCard],
    config: SchoolConfiguration,
    *,
    student_dept_cohorts: dict[str, str],
    student_level_cohorts: dict[str, str],
    student_categories: dict[str, str] | None = None,
) -> None:
    if TERM_KEYS.get(term.term_order) != "third_term":
        return

    prior_terms_stmt = select(AcademicTerm).where(
        AcademicTerm.school_id == school_id,
        AcademicTerm.academic_session_id == term.academic_session_id,
        AcademicTerm.term_order.in_([1, 2]),
        AcademicTerm.deleted_at.is_(None),
    )
    prior_terms = {
        prior.term_order: prior.id for prior in (await db.execute(prior_terms_stmt)).scalars().all()
    }
    if set(prior_terms) != {1, 2}:
        raise ValidationException(
            "Annual results need the first and second term calculations before the third term "
            "can be calculated. Please calculate the earlier terms first."
        )

    prior_results_stmt = select(SubjectResult).where(
        SubjectResult.school_id == school_id,
        SubjectResult.academic_term_id.in_(list(prior_terms.values())),
    )
    prior_results: dict[tuple[UUID, UUID, int], Decimal] = {}
    for result in (await db.execute(prior_results_stmt)).scalars().all():
        term_order = next(
            order for order, term_id in prior_terms.items() if term_id == result.academic_term_id
        )
        prior_results[(result.student_enrollment_id, result.class_subject_id, term_order)] = (
            result.total_score
        )

    for student_id, student_result in calculation.students.items():
        enrollment_id = UUID(student_id)
        for class_subject_id, subject in student_result.subjects.items():
            scores = {
                "first_term": prior_results.get((enrollment_id, UUID(class_subject_id), 1)),
                "second_term": prior_results.get((enrollment_id, UUID(class_subject_id), 2)),
                "third_term": subject.total,
            }
            if any(value is None for value in scores.values()):
                raise ValidationException(
                    "Annual results cannot be calculated because some earlier term scores are "
                    "missing. Please calculate all three terms first."
                )
            annual_score = engine.weighted_average(
                {key: value for key, value in scores.items() if value is not None},
                config.annual_results_schema,
            )
            result = subject_results.get((enrollment_id, UUID(class_subject_id)))
            if result:
                result.first_term_score = scores["first_term"]
                result.second_term_score = scores["second_term"]
                result.third_term_score = scores["third_term"]
                result.cumulative_annual_score = annual_score
                result.cumulative_annual_grade = engine.grade_for_score(
                    annual_score,
                    engine.resolve_grading_scale(
                        config.grading_scale_schema,
                        student_categories.get(student_id) if student_categories else None,
                    ),
                )[0]

    prior_overall = await _prior_overall_percentages(db, school_id, prior_terms, calculation)
    missing_prior = [
        student_id
        for student_id, values in prior_overall.items()
        if "first_term" not in values or "second_term" not in values
    ]
    if missing_prior:
        raise ValidationException(
            "Annual results cannot be calculated because some students are missing their "
            "first or second term report cards. Please calculate the earlier terms first."
        )
    annual_percentages: dict[str, Decimal] = {}
    for student_id, values in prior_overall.items():
        annual_percentages[student_id] = engine.weighted_average(
            {
                "first_term": values["first_term"],
                "second_term": values["second_term"],
                "third_term": calculation.students[student_id].percentage,
            },
            config.annual_results_schema,
        )
    dept_buckets: dict[str, dict[str, Decimal]] = {}
    level_buckets: dict[str, dict[str, Decimal]] = {}
    for student_id, pct in annual_percentages.items():
        dept_buckets.setdefault(student_dept_cohorts.get(student_id, "ALL"), {})[student_id] = pct
        level_buckets.setdefault(student_level_cohorts.get(student_id, "ALL"), {})[student_id] = pct

    positions: dict[str, int] = {}
    for bucket in dept_buckets.values():
        positions.update(engine.competition_ranks(bucket))

    level_positions: dict[str, int] = {}
    for bucket in level_buckets.values():
        level_positions.update(engine.competition_ranks(bucket))

    for student_id, percentage in annual_percentages.items():
        card = report_cards.get(UUID(student_id))
        if not card:
            continue
        card.is_cumulative_annual = True
        card.cumulative_total_obtainable = calculation.students[student_id].total_obtainable
        card.cumulative_total_obtained = calculation.students[student_id].total_obtained
        card.cumulative_average_percentage = percentage
        card.cumulative_class_position = positions[student_id]
        card.cumulative_level_position = level_positions[student_id]
        card.promotion_decision = engine.promotion_decision(
            percentage,
            engine.resolve_annual_results_schema(
                config.annual_results_schema,
                student_categories.get(student_id) if student_categories else None,
            ),
        )


async def _prior_overall_percentages(
    db: AsyncSession,
    school_id: UUID,
    prior_terms: dict[int, UUID],
    calculation: engine.TermComputation,
) -> dict[str, dict[str, Decimal]]:
    stmt = select(TermReportCard).where(
        TermReportCard.school_id == school_id,
        TermReportCard.academic_term_id.in_(list(prior_terms.values())),
    )
    result: dict[str, dict[str, Decimal]] = {student_id: {} for student_id in calculation.students}
    for card in (await db.execute(stmt)).scalars().all():
        key = str(card.student_enrollment_id)
        if key in result:
            term_order = next(
                order for order, term_id in prior_terms.items() if term_id == card.academic_term_id
            )
            result[key][TERM_KEYS[term_order]] = card.overall_percentage
    return result


async def calculate_term(
    db: AsyncSession,
    school_id: UUID,
    req: CalculateTermRequest,
    current_user: User,
    audit: AuditContext,
) -> CalculateTermResponse:
    term = await get_term(db, school_id, req.academic_term_id)
    if term.is_locked_for_scoring:
        raise ConflictException(
            "This term is already locked for scoring. Ask your school administrator to unlock it "
            "before recalculating results."
        )
    config = await _get_config(db, school_id)
    context = await _get_class_subject_context(db, school_id, term, req.class_group_id)
    if not context:
        raise ValidationException(
            "There are no class subjects available to calculate for this term."
        )
    class_groups = {class_group.id: class_group for _, class_group, _ in context}
    await _require_computation_access(db, current_user, list(class_groups.values()))
    (
        students_inputs,
        student_metadata,
        _,
        student_dept_cohorts,
        student_level_cohorts,
    ) = await _load_computation_inputs(db, school_id, term, context)
    if not students_inputs:
        raise ValidationException(
            "There are no registered students available to calculate for this term."
        )
    student_categories = await _load_student_categories(db, school_id, context, student_metadata)
    calculation = engine.compute_term_results(
        students_inputs,
        config.grading_scale_schema,
        student_dept_cohorts=student_dept_cohorts,
        student_level_cohorts=student_level_cohorts,
        student_categories=student_categories,
    )
    snapshot = build_snapshot(config)
    subject_results = await _get_existing_subject_results(db, school_id, term.id)
    report_cards = await _get_existing_report_cards(db, school_id, term.id)
    subject_results_upserted = 0

    for student_id, student_result in calculation.students.items():
        enrollment_id = UUID(student_id)
        for class_subject_id, subject in student_result.subjects.items():
            key = (enrollment_id, UUID(class_subject_id))
            result = subject_results.get(key)
            if result and result.status == "PUBLISHED":
                raise ConflictException(
                    "This term's results are already published and cannot be recalculated."
                )
            if not result:
                result = SubjectResult(
                    school_id=school_id,
                    academic_term_id=term.id,
                    class_subject_id=UUID(class_subject_id),
                    student_enrollment_id=enrollment_id,
                )
                db.add(result)
                subject_results[key] = result
            result.total_score = subject.total
            result.grade = subject.grade
            result.grade_point = subject.grade_point
            result.remark = subject.remark
            result.subject_position = subject.position or None
            result.class_highest_score = subject.class_highest_score
            result.class_lowest_score = subject.class_lowest_score
            result.class_average_score = subject.class_average_score
            result.status = "DRAFT"
            result.frozen_config_snapshot = snapshot
            subject_results_upserted += 1

        card = report_cards.get(enrollment_id)
        if card and card.publication_status == "PUBLISHED":
            raise ConflictException(
                "This term's report cards are already published and cannot be recalculated."
            )
        if not card:
            card = TermReportCard(
                school_id=school_id,
                academic_term_id=term.id,
                student_enrollment_id=enrollment_id,
            )
            db.add(card)
            report_cards[enrollment_id] = card
        card.total_obtainable_marks = student_result.total_obtainable
        card.total_marks_obtained = student_result.total_obtained
        card.overall_percentage = student_result.percentage
        card.class_position = student_result.class_position
        card.total_students_in_arm = student_result.total_students_in_class
        card.level_position = student_result.level_position
        card.total_students_in_level = student_result.total_students_in_level
        card.publication_status = "DRAFT"
        card.frozen_config_snapshot = snapshot

    await _apply_annual_data(
        db,
        school_id,
        term,
        calculation,
        subject_results,
        report_cards,
        config,
        student_dept_cohorts=student_dept_cohorts,
        student_level_cohorts=student_level_cohorts,
        student_categories=student_categories,
    )
    record_audit_log(
        db,
        audit,
        action="TERM_CALCULATED",
        resource_type="AcademicTerm",
        resource_id=term.id,
        diff_payload={
            "after": {
                "students_calculated": len(calculation.students),
                "subject_results_upserted": subject_results_upserted,
            }
        },
    )
    await _commit_or_conflict(
        db,
        message="This term's results could not be calculated. Please try again.",
        code="TERM_CALCULATION_CONFLICT",
    )
    return CalculateTermResponse(
        academic_term_id=term.id,
        class_group_id=req.class_group_id,
        students_calculated=len(calculation.students),
        subject_results_upserted=subject_results_upserted,
    )


async def get_broadsheet(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    academic_term_id: UUID | None,
    current_user: User,
    *,
    page: int = 1,
    page_size: int = 50,
) -> BroadsheetResponse:
    class_group_stmt = select(ClassGroup).where(
        ClassGroup.id == class_group_id,
        ClassGroup.school_id == school_id,
        ClassGroup.deleted_at.is_(None),
    )
    class_group = (await db.execute(class_group_stmt)).scalar_one_or_none()
    if not class_group:
        raise ResourceNotFoundException("class group", class_group_id)

    if academic_term_id is None:
        term_stmt = select(AcademicTerm).where(
            AcademicTerm.school_id == school_id,
            AcademicTerm.academic_session_id == class_group.academic_session_id,
            AcademicTerm.is_current.is_(True),
            AcademicTerm.deleted_at.is_(None),
        )
    else:
        term_stmt = select(AcademicTerm).where(
            AcademicTerm.id == academic_term_id,
            AcademicTerm.school_id == school_id,
            AcademicTerm.deleted_at.is_(None),
        )
    term = (await db.execute(term_stmt)).scalar_one_or_none()
    if not term:
        raise ResourceNotFoundException("academic term", academic_term_id or class_group_id)

    await _require_computation_access(db, current_user, [class_group], allow_form_master=True)
    config = await _get_config(db, school_id)
    academic_level = (
        await db.execute(
            select(AcademicLevel).where(
                AcademicLevel.id == class_group.academic_level_id,
                AcademicLevel.school_id == school_id,
                AcademicLevel.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    department = (
        (
            await db.execute(
                select(Department).where(
                    Department.id == class_group.department_id,
                    Department.school_id == school_id,
                    Department.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if class_group.department_id
        else None
    )
    context = await _get_class_subject_context(db, school_id, term, class_group_id)
    if not context:
        raise ValidationException("There are no class subjects available for this broadsheet yet.")
    (
        students_inputs,
        student_metadata,
        _,
        student_dept_cohorts,
        student_level_cohorts,
    ) = await _load_computation_inputs(db, school_id, term, context, require_approved=False)
    if not students_inputs:
        return BroadsheetResponse(
            academic_term_id=term.id,
            class_group_id=class_group.id,
            class_group_name=class_group.display_name,
            academic_level_id=class_group.academic_level_id,
            academic_level_name=academic_level.name if academic_level else None,
            department_name=department.name if department else None,
            academic_term_name=term.name,
            total_students=0,
            students=[],
            meta=PageMeta(page=page, page_size=page_size, total=0, total_pages=0),
        )

    student_categories = await _load_student_categories(db, school_id, context, student_metadata)
    calculation = engine.compute_term_results(
        students_inputs,
        config.grading_scale_schema,
        student_dept_cohorts=student_dept_cohorts,
        student_level_cohorts=student_level_cohorts,
        student_categories=student_categories,
        allow_pending=True,
    )
    scoped_enrollment_ids = [UUID(sid) for sid in calculation.students]
    subject_results = await _get_existing_subject_results(
        db, school_id, term.id, enrollment_ids=scoped_enrollment_ids
    )
    report_cards = await _get_existing_report_cards(
        db, school_id, term.id, enrollment_ids=scoped_enrollment_ids
    )
    rows: list[StudentBroadsheetRow] = []
    for student_id, result in calculation.students.items():
        student, enrollment = student_metadata[student_id]
        enrollment_uuid = UUID(student_id)
        card = report_cards.get(enrollment_uuid)
        annual_score = (
            card.cumulative_average_percentage if card and card.is_cumulative_annual else None
        )
        promotion = card.promotion_decision if card and card.is_cumulative_annual else None
        subjects = []
        for subject in result.subjects.values():
            persisted_sr = subject_results.get((enrollment_uuid, UUID(subject.class_subject_id)))
            is_sr_published = bool(persisted_sr and persisted_sr.status == "PUBLISHED")
            subjects.append(
                SubjectComputationResponse(
                    class_subject_id=UUID(subject.class_subject_id),
                    subject_code=subject.subject_code,
                    subject_name=subject.subject_name,
                    total_score=persisted_sr.total_score if is_sr_published else subject.total,
                    obtainable_marks=subject.obtainable,
                    grade=persisted_sr.grade if is_sr_published else subject.grade,
                    grade_point=(
                        (persisted_sr.grade_point or Decimal("0.00"))
                        if is_sr_published
                        else subject.grade_point
                    ),
                    remark=persisted_sr.remark if is_sr_published else subject.remark,
                    subject_position=(
                        persisted_sr.subject_position
                        if persisted_sr and persisted_sr.subject_position is not None
                        else subject.position
                    ),
                    class_highest_score=(
                        persisted_sr.class_highest_score
                        if persisted_sr
                        else subject.class_highest_score
                    ),
                    class_lowest_score=(
                        persisted_sr.class_lowest_score
                        if persisted_sr
                        else subject.class_lowest_score
                    ),
                    class_average_score=(
                        persisted_sr.class_average_score
                        if persisted_sr
                        else subject.class_average_score
                    ),
                    components=[
                        ComponentScoreResponse(
                            code=component.code,
                            score_status=component.score_status,
                            raw_score=component.raw_score,
                            scaled_score=component.scaled_score,
                        )
                        for component in subject.components
                    ],
                )
            )
        rows.append(
            StudentBroadsheetRow(
                student_enrollment_id=enrollment_uuid,
                admission_number=student.admission_number,
                first_name=student.first_name,
                last_name=student.last_name,
                class_group_name=class_group.display_name,
                subjects=subjects,
                total_obtained=(
                    card.total_marks_obtained
                    if card and card.publication_status == "PUBLISHED"
                    else result.total_obtained
                ),
                total_obtainable=(
                    card.total_obtainable_marks
                    if card and card.publication_status == "PUBLISHED"
                    else result.total_obtainable
                ),
                percentage=(
                    card.overall_percentage
                    if card and card.publication_status == "PUBLISHED"
                    else result.percentage
                ),
                class_position=(
                    card.class_position
                    if card and card.class_position is not None
                    else result.class_position
                ),
                total_students_in_arm=(
                    card.total_students_in_arm if card else result.total_students_in_class
                ),
                level_position=(
                    card.level_position
                    if card and card.level_position is not None
                    else result.level_position
                ),
                total_students_in_level=(
                    card.total_students_in_level
                    if card and card.total_students_in_level is not None
                    else result.total_students_in_level
                ),
                annual_score=annual_score,
                promotion_decision=promotion,
            )
        )
    rows.sort(key=lambda row: (row.class_position, row.last_name, row.first_name))
    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 0
    return BroadsheetResponse(
        academic_term_id=term.id,
        class_group_id=class_group.id,
        class_group_name=class_group.display_name,
        academic_level_id=class_group.academic_level_id,
        academic_level_name=academic_level.name if academic_level else None,
        department_name=department.name if department else None,
        academic_term_name=term.name,
        total_students=total,
        students=rows[start:end],
        meta=PageMeta(page=page, page_size=page_size, total=total, total_pages=total_pages),
    )


async def get_filtered_broadsheet(
    db: AsyncSession,
    school_id: UUID,
    *,
    academic_term_id: UUID,
    class_group_id: UUID | None,
    academic_level_id: UUID | None,
    department_id: UUID | None,
    current_user: User,
    page: int = 1,
    page_size: int = 50,
) -> BroadsheetResponse:
    if class_group_id is not None:
        return await get_broadsheet(
            db,
            school_id,
            class_group_id,
            academic_term_id,
            current_user,
            page=page,
            page_size=page_size,
        )

    term = await get_term(db, school_id, academic_term_id)
    cg_stmt = select(ClassGroup).where(
        ClassGroup.school_id == school_id,
        ClassGroup.academic_session_id == term.academic_session_id,
        ClassGroup.deleted_at.is_(None),
    )
    if academic_level_id is not None:
        cg_stmt = cg_stmt.where(ClassGroup.academic_level_id == academic_level_id)
    if department_id is not None:
        cg_stmt = cg_stmt.where(ClassGroup.department_id == department_id)
    class_groups = list((await db.execute(cg_stmt)).scalars().all())
    if not class_groups:
        raise ResourceNotFoundException("class group", academic_level_id or department_id)

    await _require_computation_access(db, current_user, class_groups, allow_form_master=True)
    config = await _get_config(db, school_id)
    academic_level = (
        (
            await db.execute(select(AcademicLevel).where(AcademicLevel.id == academic_level_id))
        ).scalar_one_or_none()
        if academic_level_id
        else None
    )
    department = (
        (
            await db.execute(select(Department).where(Department.id == department_id))
        ).scalar_one_or_none()
        if department_id
        else None
    )
    scope_label = " ".join(
        part
        for part in [
            academic_level.name if academic_level else None,
            department.name if department else None,
            "(All arms)",
        ]
        if part
    )

    cg_by_id = {cg.id: cg for cg in class_groups}
    context = await _get_class_subject_context(
        db, school_id, term, None, class_group_ids=list(cg_by_id.keys())
    )
    if not context:
        raise ValidationException("There are no class subjects available for this broadsheet yet.")
    (
        students_inputs,
        student_metadata,
        _,
        student_dept_cohorts,
        student_level_cohorts,
    ) = await _load_computation_inputs(db, school_id, term, context, require_approved=False)
    if not students_inputs:
        return BroadsheetResponse(
            academic_term_id=term.id,
            class_group_id=None,
            class_group_name=scope_label,
            academic_level_id=academic_level_id,
            academic_level_name=academic_level.name if academic_level else None,
            department_name=department.name if department else None,
            academic_term_name=term.name,
            total_students=0,
            students=[],
            meta=PageMeta(page=page, page_size=page_size, total=0, total_pages=0),
        )

    student_categories = await _load_student_categories(db, school_id, context, student_metadata)
    calculation = engine.compute_term_results(
        students_inputs,
        config.grading_scale_schema,
        student_dept_cohorts=student_dept_cohorts,
        student_level_cohorts=student_level_cohorts,
        student_categories=student_categories,
        allow_pending=True,
    )
    scoped_enrollment_ids = [UUID(sid) for sid in calculation.students]
    subject_results = await _get_existing_subject_results(
        db, school_id, term.id, enrollment_ids=scoped_enrollment_ids
    )
    report_cards = await _get_existing_report_cards(
        db, school_id, term.id, enrollment_ids=scoped_enrollment_ids
    )
    rows: list[StudentBroadsheetRow] = []
    for student_id, result in calculation.students.items():
        student, enrollment = student_metadata[student_id]
        enrollment_uuid = UUID(student_id)
        card = report_cards.get(enrollment_uuid)
        annual_score = (
            card.cumulative_average_percentage if card and card.is_cumulative_annual else None
        )
        promotion = card.promotion_decision if card and card.is_cumulative_annual else None
        cg = cg_by_id.get(enrollment.class_group_id)
        subjects = []
        for subject in result.subjects.values():
            persisted_sr = subject_results.get((enrollment_uuid, UUID(subject.class_subject_id)))
            is_sr_published = bool(persisted_sr and persisted_sr.status == "PUBLISHED")
            subjects.append(
                SubjectComputationResponse(
                    class_subject_id=UUID(subject.class_subject_id),
                    subject_code=subject.subject_code,
                    subject_name=subject.subject_name,
                    total_score=persisted_sr.total_score if is_sr_published else subject.total,
                    obtainable_marks=subject.obtainable,
                    grade=persisted_sr.grade if is_sr_published else subject.grade,
                    grade_point=(
                        (persisted_sr.grade_point or Decimal("0.00"))
                        if is_sr_published
                        else subject.grade_point
                    ),
                    remark=persisted_sr.remark if is_sr_published else subject.remark,
                    subject_position=(
                        persisted_sr.subject_position
                        if persisted_sr and persisted_sr.subject_position is not None
                        else subject.position
                    ),
                    class_highest_score=(
                        persisted_sr.class_highest_score
                        if persisted_sr
                        else subject.class_highest_score
                    ),
                    class_lowest_score=(
                        persisted_sr.class_lowest_score
                        if persisted_sr
                        else subject.class_lowest_score
                    ),
                    class_average_score=(
                        persisted_sr.class_average_score
                        if persisted_sr
                        else subject.class_average_score
                    ),
                    components=[
                        ComponentScoreResponse(
                            code=component.code,
                            score_status=component.score_status,
                            raw_score=component.raw_score,
                            scaled_score=component.scaled_score,
                        )
                        for component in subject.components
                    ],
                )
            )
        rows.append(
            StudentBroadsheetRow(
                student_enrollment_id=enrollment_uuid,
                admission_number=student.admission_number,
                first_name=student.first_name,
                last_name=student.last_name,
                class_group_name=cg.display_name if cg else None,
                subjects=subjects,
                total_obtained=(
                    card.total_marks_obtained
                    if card and card.publication_status == "PUBLISHED"
                    else result.total_obtained
                ),
                total_obtainable=(
                    card.total_obtainable_marks
                    if card and card.publication_status == "PUBLISHED"
                    else result.total_obtainable
                ),
                percentage=(
                    card.overall_percentage
                    if card and card.publication_status == "PUBLISHED"
                    else result.percentage
                ),
                class_position=(
                    card.class_position
                    if card and card.class_position is not None
                    else result.class_position
                ),
                total_students_in_arm=(
                    card.total_students_in_arm if card else result.total_students_in_class
                ),
                level_position=(
                    card.level_position
                    if card and card.level_position is not None
                    else result.level_position
                ),
                total_students_in_level=(
                    card.total_students_in_level
                    if card and card.total_students_in_level is not None
                    else result.total_students_in_level
                ),
                annual_score=annual_score,
                promotion_decision=promotion,
            )
        )
    rows.sort(key=lambda row: (row.class_position, row.last_name, row.first_name))
    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 0
    return BroadsheetResponse(
        academic_term_id=term.id,
        class_group_id=None,
        class_group_name=scope_label,
        academic_level_id=academic_level_id,
        academic_level_name=academic_level.name if academic_level else None,
        department_name=department.name if department else None,
        academic_term_name=term.name,
        total_students=total,
        students=rows[start:end],
        meta=PageMeta(page=page, page_size=page_size, total=total, total_pages=total_pages),
    )


def promotion_decision_values() -> list[str]:
    return [decision.value for decision in PromotionDecision]


async def export_broadsheet(
    db: AsyncSession,
    school_id: UUID,
    *,
    academic_term_id: UUID,
    class_group_id: UUID | None,
    academic_level_id: UUID | None,
    department_id: UUID | None,
    current_user: User,
) -> tuple[bytes, str]:
    broadsheet = await get_filtered_broadsheet(
        db,
        school_id,
        academic_term_id=academic_term_id,
        class_group_id=class_group_id,
        academic_level_id=academic_level_id,
        department_id=department_id,
        current_user=current_user,
        page=1,
        page_size=2000,
    )
    school = (await db.execute(select(School).where(School.id == school_id))).scalar_one_or_none()
    school_name = school.name if school else "School"
    xlsx_bytes = broadsheet_export.build_broadsheet_xlsx(school_name, broadsheet)
    safe_scope = broadsheet.class_group_name.replace(" ", "_").replace("/", "-")
    safe_term = broadsheet.academic_term_name.replace(" ", "_")
    return xlsx_bytes, f"BROADSHEET_{safe_scope}_{safe_term}.xlsx"
