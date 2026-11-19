import hmac
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    ConflictException,
    InsufficientPermissionException,
    ResourceNotFoundException,
    ValidationException,
)
from app.core.permissions import user_has_permission
from app.core.redis import get_redis
from app.core.storage import storage_service
from app.modules.academic_structure.models import (
    AcademicLevel,
    AcademicSession,
    AcademicTerm,
    ClassGroup,
    Department,
)
from app.modules.assessments.models import AssessmentDefinition, AssessmentScore
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.computation.models import SubjectResult, TermReportCard
from app.modules.distribution.models import ResultAccessToken
from app.modules.distribution.schemas import (
    NotifyTermResultsRequest,
    NotifyTermResultsResponse,
    QrVerifyResponse,
    ReportAttendanceSummary,
    ReportCardListItem,
    ReportCardListResponse,
    ReportCardListSummary,
    ReportCardRemarksResponse,
    ReportCardRemarksUpdateRequest,
    ReportSchoolInfo,
    ReportStudentInfo,
    ReportSubjectItem,
    ReportSummaryInfo,
    ReportVerificationInfo,
    VerifiedReportCardResponse,
)
from app.modules.distribution.tasks import send_term_result_notifications
from app.modules.identity.models import School, SchoolConfiguration, User
from app.modules.students.models import Student, StudentEnrollment
from app.modules.subjects.models import ClassSubject, Subject, SubjectOffering

VERIFY_RATE_LIMIT_PER_MINUTE = 20


def _hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _sign_qr_payload(admission_number: str, term_code: str, total_obtained: Decimal) -> str:
    secret = get_settings().SECRET_KEY.encode("utf-8")
    body = f"VERIFY:{admission_number}:{term_code}:{total_obtained:.2f}"
    sig = hmac.new(secret, body.encode("utf-8"), sha256).hexdigest()[:12]
    return f"{body}:SIG_{sig}"


def verify_qr_payload(payload: str) -> QrVerifyResponse:
    parts = payload.strip().split(":")
    if len(parts) != 5 or parts[0] != "VERIFY" or not parts[4].startswith("SIG_"):
        raise ValidationException("This verification QR code is invalid or malformed.")
    _, admission_number, term_code, total_obtained, sig_part = parts
    body = f"VERIFY:{admission_number}:{term_code}:{total_obtained}"
    secret = get_settings().SECRET_KEY.encode("utf-8")
    expected_sig = hmac.new(secret, body.encode("utf-8"), sha256).hexdigest()[:12]
    provided_sig = sig_part.removeprefix("SIG_")
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise ValidationException(
            "This report card QR signature could not be verified. It may have been altered."
        )
    return QrVerifyResponse(
        is_authentic=True,
        admission_number=admission_number,
        term_code=term_code,
        total_obtained=total_obtained,
    )


async def _enforce_verify_rate_limit(client_ip: str | None) -> None:
    if not client_ip:
        return
    try:
        redis = await get_redis()
    except Exception:
        return
    key = f"rl:results_verify:{client_ip}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count > VERIFY_RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many verification attempts. Please wait a minute and try again.",
            )
    except HTTPException:
        raise
    except Exception:
        return


async def verify_result_token(
    db: AsyncSession,
    raw_token: str,
    *,
    dob: date | None = None,
    admission_number: str | None = None,
    client_ip: str | None = None,
) -> VerifiedReportCardResponse:
    await _enforce_verify_rate_limit(client_ip)
    if dob is None and (not admission_number or not admission_number.strip()):
        raise ValidationException(
            "Please enter the student's date of birth or admission number to view this report card."
        )

    await db.execute(text("SELECT set_config('app.is_super_admin', 'true', true)"))
    token_hash = _hash_token(raw_token)
    token = (
        await db.execute(
            select(ResultAccessToken).where(ResultAccessToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    await db.execute(text("SELECT set_config('app.is_super_admin', 'false', true)"))

    if token is None or token.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This result link is invalid or has been revoked.",
        )

    now = datetime.now(UTC)
    if token.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This result link has expired. Please request a fresh link from your school.",
        )
    if token.access_count >= token.max_access_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="This result link has reached its maximum view limit.",
        )

    await db.execute(
        text(
            "SELECT set_config('app.current_school_id', :school_id, true), "
            "set_config('app.is_super_admin', 'false', true)"
        ),
        {"school_id": str(token.school_id)},
    )

    card = (
        await db.execute(
            select(TermReportCard).where(TermReportCard.id == token.term_report_card_id)
        )
    ).scalar_one_or_none()
    if card is None or card.publication_status != "PUBLISHED":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This report card is not available for viewing.",
        )

    enrollment = (
        await db.execute(
            select(StudentEnrollment).where(StudentEnrollment.id == card.student_enrollment_id)
        )
    ).scalar_one()
    student = (
        await db.execute(select(Student).where(Student.id == enrollment.student_id))
    ).scalar_one()

    dob_ok = dob is not None and student.date_of_birth == dob
    adm_ok = (
        admission_number is not None
        and student.admission_number.strip().lower() == admission_number.strip().lower()
    )
    if not (dob_ok or adm_ok):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The verification details do not match this student's record. Please check and try again.",
        )

    inc_stmt = (
        update(ResultAccessToken)
        .where(
            ResultAccessToken.id == token.id,
            ResultAccessToken.access_count < ResultAccessToken.max_access_limit,
        )
        .values(
            access_count=ResultAccessToken.access_count + 1,
            last_accessed_at=now,
            last_accessed_ip=client_ip if client_ip else ResultAccessToken.last_accessed_ip,
        )
        .returning(ResultAccessToken.access_count)
    )
    updated_count = (await db.execute(inc_stmt)).scalar_one_or_none()
    if updated_count is None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="This result link has reached its maximum view limit.",
        )

    school = (await db.execute(select(School).where(School.id == token.school_id))).scalar_one()
    config = (
        await db.execute(
            select(SchoolConfiguration).where(SchoolConfiguration.school_id == token.school_id)
        )
    ).scalar_one_or_none()
    term = (
        await db.execute(select(AcademicTerm).where(AcademicTerm.id == card.academic_term_id))
    ).scalar_one()
    session = (
        await db.execute(
            select(AcademicSession).where(AcademicSession.id == term.academic_session_id)
        )
    ).scalar_one()
    class_group = (
        await db.execute(select(ClassGroup).where(ClassGroup.id == enrollment.class_group_id))
    ).scalar_one()
    level = (
        await db.execute(
            select(AcademicLevel).where(AcademicLevel.id == class_group.academic_level_id)
        )
    ).scalar_one()
    department_name: str | None = None
    if class_group.department_id is not None:
        dept = (
            await db.execute(select(Department).where(Department.id == class_group.department_id))
        ).scalar_one_or_none()
        department_name = dept.name if dept else None

    subject_rows = (
        await db.execute(
            select(SubjectResult, Subject)
            .join(ClassSubject, ClassSubject.id == SubjectResult.class_subject_id)
            .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
            .join(Subject, Subject.id == SubjectOffering.subject_id)
            .where(
                SubjectResult.school_id == token.school_id,
                SubjectResult.academic_term_id == card.academic_term_id,
                SubjectResult.student_enrollment_id == card.student_enrollment_id,
                SubjectResult.deleted_at.is_(None),
            )
            .order_by(Subject.name.asc())
        )
    ).all()

    score_rows = (
        await db.execute(
            select(
                AssessmentDefinition.class_subject_id,
                AssessmentDefinition.assessment_type,
                AssessmentDefinition.max_score,
                AssessmentDefinition.weight_percentage,
                AssessmentScore.score_status,
                AssessmentScore.raw_score,
            )
            .join(
                AssessmentDefinition,
                AssessmentDefinition.id == AssessmentScore.assessment_definition_id,
            )
            .where(
                AssessmentScore.school_id == token.school_id,
                AssessmentScore.student_enrollment_id == card.student_enrollment_id,
                AssessmentDefinition.academic_term_id == card.academic_term_id,
            )
        )
    ).all()

    ca_totals: dict[UUID, Decimal] = {}
    exam_totals: dict[UUID, Decimal] = {}
    for cs_id, assessment_type, max_score, weight_percentage, score_status, raw_score in score_rows:
        if score_status != "SCORED" or raw_score is None or not max_score or max_score <= 0:
            val = Decimal("0.00")
        else:
            val = ((raw_score / max_score) * weight_percentage).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        if assessment_type == "EXAMINATION":
            exam_totals[cs_id] = exam_totals.get(cs_id, Decimal("0.00")) + val
        else:
            ca_totals[cs_id] = ca_totals.get(cs_id, Decimal("0.00")) + val

    await db.commit()

    full_name = " ".join(
        part for part in (student.first_name, student.middle_name, student.last_name) if part
    )
    term_code = f"{session.name}-T{term.term_order}"
    qr_payload = _sign_qr_payload(student.admission_number, term_code, card.total_marks_obtained)

    frozen_template = (card.frozen_config_snapshot or {}).get("report_card_template_id")
    template_id = frozen_template or (config.report_card_template_id if config else "default_v1")

    return VerifiedReportCardResponse(
        school=ReportSchoolInfo(
            name=school.name,
            school_type=school.school_type,
            address=school.address,
            city=school.city,
            state=school.state,
            logo_url=storage_service.public_url(school.logo_storage_path),
            stamp_url=storage_service.public_url(school.stamp_image_url),
            principal_signature_url=storage_service.public_url(school.principal_signature_url),
            report_card_template_id=template_id,
        ),
        student=ReportStudentInfo(
            admission_number=student.admission_number,
            full_name=full_name,
            academic_level=level.name,
            department=department_name,
            class_group=class_group.display_name,
            academic_session=session.name,
            term=term.name,
        ),
        attendance=ReportAttendanceSummary(
            times_school_opened=card.times_school_opened,
            times_present=card.times_present,
            times_absent=card.times_absent,
        ),
        summary=ReportSummaryInfo(
            total_subjects=len(subject_rows),
            total_obtainable=card.total_obtainable_marks,
            total_obtained=card.total_marks_obtained,
            percentage=card.overall_percentage,
            class_position=card.class_position,
            total_students_in_arm=card.total_students_in_arm,
            class_teacher_comment=card.teacher_comment,
            principal_comment=card.principal_comment,
            annual_cumulative_score=card.cumulative_average_percentage,
            promotion_decision=card.promotion_decision,
        ),
        subject_breakdown=[
            ReportSubjectItem(
                subject_code=subject.code,
                subject_name=subject.name,
                test_score=ca_totals.get(res.class_subject_id, Decimal("0.00")),
                exam_score=exam_totals.get(res.class_subject_id, Decimal("0.00")),
                total_score=res.total_score,
                grade=res.grade,
                remark=res.remark,
                subject_position=res.subject_position,
                class_average=res.class_average_score,
                first_term_score=res.first_term_score,
                second_term_score=res.second_term_score,
                third_term_score=res.third_term_score,
                cumulative_annual_score=res.cumulative_annual_score,
                cumulative_annual_grade=res.cumulative_annual_grade,
            )
            for res, subject in subject_rows
        ],
        verification=ReportVerificationInfo(
            qr_payload=qr_payload,
            issued_at=card.published_at or now,
            is_authentic=True,
        ),
    )


async def update_report_card_remarks(
    db: AsyncSession,
    school_id: UUID,
    report_card_id: UUID,
    req: ReportCardRemarksUpdateRequest,
    current_user: User,
    audit: AuditContext,
) -> ReportCardRemarksResponse:
    card = (
        await db.execute(
            select(TermReportCard).where(
                TermReportCard.id == report_card_id,
                TermReportCard.school_id == school_id,
                TermReportCard.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if card is None:
        raise ResourceNotFoundException("report card", report_card_id)
    if card.publication_status == "PUBLISHED":
        raise ConflictException(
            "Remarks cannot be edited because this report card has already been published."
        )

    enrollment = (
        await db.execute(
            select(StudentEnrollment).where(StudentEnrollment.id == card.student_enrollment_id)
        )
    ).scalar_one()
    class_group = (
        await db.execute(select(ClassGroup).where(ClassGroup.id == enrollment.class_group_id))
    ).scalar_one()

    is_class_teacher = class_group.class_teacher_id == current_user.id
    can_publish = await user_has_permission(
        db,
        current_user,
        "reports:publish",
        department_id=class_group.department_id,
        academic_level_id=class_group.academic_level_id,
        class_group_id=class_group.id,
    )
    if not (is_class_teacher or can_publish):
        raise InsufficientPermissionException("reports:publish")

    if req.principal_comment is not None and not can_publish:
        raise InsufficientPermissionException("reports:publish")

    if req.teacher_comment is not None:
        card.teacher_comment = req.teacher_comment.strip() or None
    if req.principal_comment is not None:
        card.principal_comment = req.principal_comment.strip() or None

    record_audit_log(
        db,
        audit,
        action="REPORT_CARD_REMARKS_UPDATED",
        resource_type="TermReportCard",
        resource_id=card.id,
        diff_payload={
            "teacher_comment": card.teacher_comment,
            "principal_comment": card.principal_comment,
        },
    )
    await db.commit()
    return ReportCardRemarksResponse(
        id=card.id,
        student_enrollment_id=card.student_enrollment_id,
        academic_term_id=card.academic_term_id,
        teacher_comment=card.teacher_comment,
        principal_comment=card.principal_comment,
    )


async def queue_term_result_notifications(
    db: AsyncSession,
    school_id: UUID,
    req: NotifyTermResultsRequest,
    audit: AuditContext,
) -> NotifyTermResultsResponse:
    school = (await db.execute(select(School).where(School.id == school_id))).scalar_one()
    term = (
        await db.execute(
            select(AcademicTerm).where(
                AcademicTerm.id == req.academic_term_id,
                AcademicTerm.school_id == school_id,
                AcademicTerm.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if term is None:
        raise ResourceNotFoundException("academic term", req.academic_term_id)
    session = (
        await db.execute(
            select(AcademicSession).where(AcademicSession.id == term.academic_session_id)
        )
    ).scalar_one()

    enrollment_ids = [item.student_enrollment_id for item in req.result_links]
    token_hashes = [_hash_token(item.token) for item in req.result_links]

    rows = (
        await db.execute(
            select(StudentEnrollment.id, Student, ResultAccessToken.token_hash)
            .join(Student, Student.id == StudentEnrollment.student_id)
            .join(
                TermReportCard,
                TermReportCard.student_enrollment_id == StudentEnrollment.id,
            )
            .join(
                ResultAccessToken,
                ResultAccessToken.term_report_card_id == TermReportCard.id,
            )
            .where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.id.in_(enrollment_ids),
                TermReportCard.academic_term_id == term.id,
                TermReportCard.publication_status == "PUBLISHED",
                ResultAccessToken.token_hash.in_(token_hashes),
                ResultAccessToken.is_revoked.is_(False),
            )
        )
    ).all()
    verified_pairs = {(eid, th): stu for eid, stu, th in rows}

    recipients: list[dict[str, str]] = []
    for item in req.result_links:
        student = verified_pairs.get((item.student_enrollment_id, _hash_token(item.token)))
        if student is None or not student.guardian_email:
            continue
        recipients.append(
            {
                "token": item.token,
                "guardian_email": student.guardian_email,
                "guardian_name": student.guardian_name,
                "student_name": f"{student.first_name} {student.last_name}",
            }
        )

    if recipients:
        send_term_result_notifications.delay(
            school_name=school.name,
            term_name=term.name,
            session_name=session.name,
            recipients=recipients,
        )

    record_audit_log(
        db,
        audit,
        action="TERM_RESULT_NOTIFICATIONS_QUEUED",
        resource_type="AcademicTerm",
        resource_id=term.id,
        diff_payload={"notifications_queued": len(recipients)},
    )
    await db.commit()
    return NotifyTermResultsResponse(
        academic_term_id=term.id,
        notifications_queued=len(recipients),
        message=f"Queued {len(recipients)} parent result notification(s).",
    )


async def list_report_cards(
    db: AsyncSession,
    school_id: UUID,
    academic_term_id: UUID,
    class_group_id: UUID,
    current_user: User,
    *,
    promotion_decision: str | None = None,
) -> ReportCardListResponse:
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

    group = (
        await db.execute(
            select(ClassGroup).where(
                ClassGroup.id == class_group_id,
                ClassGroup.school_id == school_id,
                ClassGroup.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if group is None:
        raise ResourceNotFoundException("class group", class_group_id)

    can_publish = await user_has_permission(
        db,
        current_user,
        "reports:publish",
        department_id=group.department_id,
        academic_level_id=group.academic_level_id,
        class_group_id=group.id,
    )
    if not can_publish and group.class_teacher_id != current_user.id:
        raise InsufficientPermissionException(permission_code="reports:publish")

    config = (
        await db.execute(
            select(SchoolConfiguration).where(SchoolConfiguration.school_id == school_id)
        )
    ).scalar_one_or_none()
    thresholds = (
        config.annual_results_schema.get("promotion_thresholds", {})
        if config and config.annual_results_schema
        else {}
    )
    pass_mark = Decimal(str(thresholds.get("promoted_min_percentage", "50.00")))

    rows = (
        await db.execute(
            select(TermReportCard, Student)
            .join(StudentEnrollment, StudentEnrollment.id == TermReportCard.student_enrollment_id)
            .join(Student, Student.id == StudentEnrollment.student_id)
            .where(
                TermReportCard.school_id == school_id,
                TermReportCard.academic_term_id == term.id,
                StudentEnrollment.class_group_id == group.id,
                TermReportCard.deleted_at.is_(None),
            )
            .order_by(
                TermReportCard.class_position.asc().nulls_last(),
                Student.last_name.asc(),
                Student.first_name.asc(),
            )
        )
    ).all()

    averages = [
        card.cumulative_average_percentage
        for card, _ in rows
        if card.cumulative_average_percentage is not None
    ]
    class_average = (
        str(
            (sum(averages, Decimal("0.00")) / Decimal(len(averages))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
        if averages
        else None
    )
    passing = sum(1 for avg in averages if avg >= pass_mark)
    pass_rate = (
        float(
            ((Decimal(passing) / Decimal(len(averages))) * Decimal("100")).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        )
        if averages
        else None
    )

    filtered_rows = rows
    if promotion_decision:
        dec = promotion_decision.strip().upper()
        filtered_rows = [(c, s) for c, s in rows if (c.promotion_decision or "").upper() == dec]

    items = [
        ReportCardListItem(
            id=card.id,
            student_enrollment_id=card.student_enrollment_id,
            student_id=student.id,
            admission_number=student.admission_number,
            student_name=f"{student.first_name} {student.last_name}".strip(),
            cumulative_average_percentage=(
                str(card.cumulative_average_percentage)
                if card.cumulative_average_percentage is not None
                else None
            ),
            class_position=card.class_position,
            promotion_decision=card.promotion_decision,
            teacher_comment=card.teacher_comment,
            principal_comment=card.principal_comment,
            publication_status=card.publication_status,
        )
        for card, student in filtered_rows
    ]

    term_status = (
        "PUBLISHED"
        if any(card.publication_status == "PUBLISHED" for card, _ in rows)
        else ("LOCKED" if term.is_locked_for_scoring else "DRAFT")
    )

    return ReportCardListResponse(
        items=items,
        summary=ReportCardListSummary(
            students_computed=len(rows),
            class_average=class_average,
            pass_rate=pass_rate,
            term_status=term_status,
        ),
    )
