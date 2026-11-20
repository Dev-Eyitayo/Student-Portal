from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    InsufficientPermissionException,
    ResourceNotFoundException,
    ValidationException,
)
from app.core.permissions import require_scoped_permission
from app.modules.academic_structure.models import AcademicTerm, ClassGroup, Department
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.common.pagination import paginate
from app.modules.common.schemas import PageMeta
from app.modules.identity.models import SchoolConfiguration, User
from app.modules.students.models import (
    AttendanceRecord,
    AttendanceSummary,
    Student,
    StudentEnrollment,
)
from app.modules.students.schemas import (
    AttendanceSummaryUpdateRequest,
    ClassAttendanceMarkRequest,
    ClassAttendanceMarkResponse,
    ClassAttendanceSummaryUpdateRequest,
    EnrollmentAttendanceMarkRequest,
)


async def _get_class_group(db: AsyncSession, school_id: UUID, class_group_id: UUID) -> ClassGroup:
    stmt = select(ClassGroup).where(
        ClassGroup.id == class_group_id,
        ClassGroup.school_id == school_id,
        ClassGroup.deleted_at.is_(None),
    )
    group = (await db.execute(stmt)).scalar_one_or_none()
    if group is None:
        raise ResourceNotFoundException("class group", class_group_id)
    return group


async def _get_term(db: AsyncSession, school_id: UUID, term_id: UUID) -> AcademicTerm:
    stmt = select(AcademicTerm).where(
        AcademicTerm.id == term_id,
        AcademicTerm.school_id == school_id,
        AcademicTerm.deleted_at.is_(None),
    )
    term = (await db.execute(stmt)).scalar_one_or_none()
    if term is None:
        raise ResourceNotFoundException("academic term", term_id)
    return term


async def _get_enrollment(
    db: AsyncSession, school_id: UUID, enrollment_id: UUID
) -> StudentEnrollment:
    stmt = select(StudentEnrollment).where(
        StudentEnrollment.id == enrollment_id,
        StudentEnrollment.school_id == school_id,
        StudentEnrollment.deleted_at.is_(None),
    )
    enrollment = (await db.execute(stmt)).scalar_one_or_none()
    if enrollment is None:
        raise ResourceNotFoundException("student enrollment", enrollment_id)
    return enrollment


async def _school_today(db: AsyncSession, school_id: UUID) -> date:
    tz_name = (
        await db.execute(
            select(SchoolConfiguration.timezone).where(SchoolConfiguration.school_id == school_id)
        )
    ).scalar_one_or_none()
    if tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name)).date()
        except ZoneInfoNotFoundError:
            pass
    return datetime.now(UTC).date()


async def _require_attendance_access(
    db: AsyncSession,
    current_user: User,
    class_group: ClassGroup,
) -> None:
    if class_group.class_teacher_id == current_user.id:
        return
    try:
        await require_scoped_permission(
            db,
            current_user,
            "students:enroll",
            department_id=class_group.department_id,
            academic_level_id=class_group.academic_level_id,
            class_group_id=class_group.id,
        )
    except InsufficientPermissionException:
        await require_scoped_permission(
            db,
            current_user,
            "structure:manage",
            department_id=class_group.department_id,
            academic_level_id=class_group.academic_level_id,
            class_group_id=class_group.id,
        )


def _validate_attendance_date(
    term: AcademicTerm, attendance_date: date, today: date, reason: str | None
) -> None:
    if attendance_date > today:
        raise ValidationException(
            "Attendance cannot be marked for a future date. Please choose today or an earlier date."
        )
    if attendance_date < term.start_date or attendance_date > term.end_date:
        raise ValidationException(
            "This attendance date falls outside the selected term's start and end dates."
        )
    if attendance_date < today and (not reason or not reason.strip()):
        raise ValidationException(
            "Please provide a short reason when recording or updating backdated attendance."
        )


async def _sync_daily_summary(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    term_id: UUID,
    actor_id: UUID,
) -> None:
    summary = (
        await db.execute(
            select(AttendanceSummary).where(
                AttendanceSummary.school_id == school_id,
                AttendanceSummary.student_enrollment_id == enrollment_id,
                AttendanceSummary.academic_term_id == term_id,
            )
        )
    ).scalar_one_or_none()
    if summary is not None and summary.source == "MANUAL":
        return

    rows = (
        await db.execute(
            select(AttendanceRecord.status, func.count(AttendanceRecord.id))
            .where(
                AttendanceRecord.school_id == school_id,
                AttendanceRecord.student_enrollment_id == enrollment_id,
                AttendanceRecord.academic_term_id == term_id,
                AttendanceRecord.deleted_at.is_(None),
            )
            .group_by(AttendanceRecord.status)
        )
    ).all()
    counts = {status: count for status, count in rows}
    present = counts.get("PRESENT", 0) + counts.get("LATE", 0)
    absent = counts.get("ABSENT", 0)
    opened = present + absent

    if summary is None:
        summary = AttendanceSummary(
            school_id=school_id,
            student_enrollment_id=enrollment_id,
            academic_term_id=term_id,
            times_school_opened=opened,
            times_present=present,
            times_absent=absent,
            source="DAILY",
            updated_by_id=actor_id,
        )
        db.add(summary)
    else:
        summary.times_school_opened = opened
        summary.times_present = present
        summary.times_absent = absent
        summary.deleted_at = None
        summary.updated_by_id = actor_id


async def mark_class_attendance(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    req: ClassAttendanceMarkRequest,
    current_user: User,
    audit: AuditContext,
) -> ClassAttendanceMarkResponse:
    group = await _get_class_group(db, school_id, class_group_id)
    await _require_attendance_access(db, current_user, group)
    term = await _get_term(db, school_id, req.academic_term_id)
    if term.academic_session_id != group.academic_session_id:
        raise ValidationException(
            "This term does not belong to the same academic session as the selected class."
        )
    if term.is_locked_for_scoring:
        raise ValidationException(
            "Attendance cannot be modified because this term is already locked."
        )

    today = await _school_today(db, school_id)
    _validate_attendance_date(term, req.attendance_date, today, req.reason)

    enrollment_ids = [item.student_enrollment_id for item in req.records]
    if len(enrollment_ids) != len(set(enrollment_ids)):
        raise ValidationException(
            "Each learner can only appear once in a single class attendance submission."
        )

    valid_enrollments = set(
        (
            await db.execute(
                select(StudentEnrollment.id).where(
                    StudentEnrollment.school_id == school_id,
                    StudentEnrollment.class_group_id == group.id,
                    StudentEnrollment.id.in_(enrollment_ids),
                    StudentEnrollment.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(valid_enrollments) != len(enrollment_ids):
        raise ValidationException(
            "One or more learners in this attendance submission do not belong to the selected class."
        )

    existing_records = {
        rec.student_enrollment_id: rec
        for rec in (
            await db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.school_id == school_id,
                    AttendanceRecord.student_enrollment_id.in_(enrollment_ids),
                    AttendanceRecord.attendance_date == req.attendance_date,
                )
            )
        )
        .scalars()
        .all()
    }

    reason_clean = req.reason.strip() if req.reason else None
    for item in req.records:
        existing = existing_records.get(item.student_enrollment_id)
        if existing is None:
            db.add(
                AttendanceRecord(
                    school_id=school_id,
                    student_enrollment_id=item.student_enrollment_id,
                    academic_term_id=term.id,
                    attendance_date=req.attendance_date,
                    status=item.status.value,
                    reason=reason_clean,
                    recorded_by_id=current_user.id,
                )
            )
        else:
            existing.status = item.status.value
            existing.academic_term_id = term.id
            existing.reason = reason_clean
            existing.deleted_at = None
            existing.recorded_by_id = current_user.id

    await db.flush()
    for enrollment_id in valid_enrollments:
        await _sync_daily_summary(db, school_id, enrollment_id, term.id, current_user.id)

    record_audit_log(
        db,
        audit,
        action="CLASS_ATTENDANCE_MARKED",
        resource_type="ClassGroup",
        resource_id=group.id,
        diff_payload={
            "academic_term_id": str(term.id),
            "attendance_date": req.attendance_date.isoformat(),
            "records_saved": len(req.records),
            "reason": reason_clean,
        },
    )
    await db.commit()
    return ClassAttendanceMarkResponse(
        class_group_id=group.id,
        academic_term_id=term.id,
        attendance_date=req.attendance_date,
        records_saved=len(req.records),
    )


async def mark_enrollment_attendance(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    req: EnrollmentAttendanceMarkRequest,
    current_user: User,
    audit: AuditContext,
) -> AttendanceRecord:
    enrollment = await _get_enrollment(db, school_id, enrollment_id)
    group = await _get_class_group(db, school_id, enrollment.class_group_id)
    await _require_attendance_access(db, current_user, group)
    term = await _get_term(db, school_id, req.academic_term_id)
    if term.academic_session_id != enrollment.academic_session_id:
        raise ValidationException(
            "This term does not belong to the same academic session as the learner's enrollment."
        )
    if term.is_locked_for_scoring:
        raise ValidationException(
            "Attendance cannot be modified because this term is already locked."
        )

    today = await _school_today(db, school_id)
    _validate_attendance_date(term, req.attendance_date, today, req.reason)

    reason_clean = req.reason.strip() if req.reason else None
    record = (
        await db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.school_id == school_id,
                AttendanceRecord.student_enrollment_id == enrollment.id,
                AttendanceRecord.attendance_date == req.attendance_date,
            )
        )
    ).scalar_one_or_none()

    if record is None:
        record = AttendanceRecord(
            school_id=school_id,
            student_enrollment_id=enrollment.id,
            academic_term_id=term.id,
            attendance_date=req.attendance_date,
            status=req.status.value,
            reason=reason_clean,
            recorded_by_id=current_user.id,
        )
        db.add(record)
    else:
        record.status = req.status.value
        record.academic_term_id = term.id
        record.reason = reason_clean
        record.deleted_at = None
        record.recorded_by_id = current_user.id

    await db.flush()
    await _sync_daily_summary(db, school_id, enrollment.id, term.id, current_user.id)
    record_audit_log(
        db,
        audit,
        action="ENROLLMENT_ATTENDANCE_MARKED",
        resource_type="StudentEnrollment",
        resource_id=enrollment.id,
        diff_payload={
            "academic_term_id": str(term.id),
            "attendance_date": req.attendance_date.isoformat(),
            "status": req.status.value,
            "reason": reason_clean,
        },
    )
    await db.commit()
    await db.refresh(record)
    return record


async def list_enrollment_attendance(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    current_user: User,
    *,
    academic_term_id: UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AttendanceRecord], PageMeta]:
    enrollment = await _get_enrollment(db, school_id, enrollment_id)
    group = await _get_class_group(db, school_id, enrollment.class_group_id)
    await _require_attendance_access(db, current_user, group)

    stmt: Select[tuple[AttendanceRecord]] = select(AttendanceRecord).where(
        AttendanceRecord.school_id == school_id,
        AttendanceRecord.student_enrollment_id == enrollment.id,
        AttendanceRecord.deleted_at.is_(None),
    )
    if academic_term_id is not None:
        stmt = stmt.where(AttendanceRecord.academic_term_id == academic_term_id)
    stmt = stmt.order_by(AttendanceRecord.attendance_date.desc())
    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_enrollment_attendance_summary(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    academic_term_id: UUID,
    current_user: User,
) -> AttendanceSummary:
    enrollment = await _get_enrollment(db, school_id, enrollment_id)
    group = await _get_class_group(db, school_id, enrollment.class_group_id)
    await _require_attendance_access(db, current_user, group)
    term = await _get_term(db, school_id, academic_term_id)
    if term.academic_session_id != enrollment.academic_session_id:
        raise ValidationException(
            "This term does not belong to the same academic session as the learner's enrollment."
        )

    summary = (
        await db.execute(
            select(AttendanceSummary).where(
                AttendanceSummary.school_id == school_id,
                AttendanceSummary.student_enrollment_id == enrollment.id,
                AttendanceSummary.academic_term_id == academic_term_id,
                AttendanceSummary.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if summary is None:
        raise ResourceNotFoundException("attendance summary", enrollment_id)
    return summary


async def _upsert_manual_summary(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    term_id: UUID,
    *,
    opened: int,
    present: int,
    absent: int,
    reason: str,
    actor_id: UUID,
) -> AttendanceSummary:
    summary = (
        await db.execute(
            select(AttendanceSummary).where(
                AttendanceSummary.school_id == school_id,
                AttendanceSummary.student_enrollment_id == enrollment_id,
                AttendanceSummary.academic_term_id == term_id,
            )
        )
    ).scalar_one_or_none()
    if summary is None:
        summary = AttendanceSummary(
            school_id=school_id,
            student_enrollment_id=enrollment_id,
            academic_term_id=term_id,
            times_school_opened=opened,
            times_present=present,
            times_absent=absent,
            source="MANUAL",
            override_reason=reason,
            updated_by_id=actor_id,
        )
        db.add(summary)
    else:
        summary.times_school_opened = opened
        summary.times_present = present
        summary.times_absent = absent
        summary.source = "MANUAL"
        summary.override_reason = reason
        summary.deleted_at = None
        summary.updated_by_id = actor_id
    return summary


async def update_enrollment_attendance_summary(
    db: AsyncSession,
    school_id: UUID,
    enrollment_id: UUID,
    req: AttendanceSummaryUpdateRequest,
    current_user: User,
    audit: AuditContext,
) -> AttendanceSummary:
    enrollment = await _get_enrollment(db, school_id, enrollment_id)
    group = await _get_class_group(db, school_id, enrollment.class_group_id)
    await _require_attendance_access(db, current_user, group)
    term = await _get_term(db, school_id, req.academic_term_id)
    if term.academic_session_id != enrollment.academic_session_id:
        raise ValidationException(
            "This term does not belong to the same academic session as the learner's enrollment."
        )
    if term.is_locked_for_scoring:
        raise ValidationException(
            "Attendance cannot be modified because this term is already locked."
        )

    summary = await _upsert_manual_summary(
        db,
        school_id,
        enrollment.id,
        term.id,
        opened=req.times_school_opened,
        present=req.times_present,
        absent=req.times_absent,
        reason=req.override_reason,
        actor_id=current_user.id,
    )
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ATTENDANCE_SUMMARY_OVERRIDDEN",
        resource_type="AttendanceSummary",
        resource_id=summary.id,
        diff_payload={
            "academic_term_id": str(term.id),
            "times_school_opened": req.times_school_opened,
            "times_present": req.times_present,
            "times_absent": req.times_absent,
            "reason": req.override_reason,
        },
    )
    await db.commit()
    await db.refresh(summary)
    return summary


async def update_class_attendance_summary(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    req: ClassAttendanceSummaryUpdateRequest,
    current_user: User,
    audit: AuditContext,
) -> list[AttendanceSummary]:
    group = await _get_class_group(db, school_id, class_group_id)
    await _require_attendance_access(db, current_user, group)
    term = await _get_term(db, school_id, req.academic_term_id)
    if term.academic_session_id != group.academic_session_id:
        raise ValidationException(
            "This term does not belong to the same academic session as the selected class."
        )
    if term.is_locked_for_scoring:
        raise ValidationException(
            "Attendance cannot be modified because this term is already locked."
        )

    enrollment_ids = [item.student_enrollment_id for item in req.summaries]
    if len(enrollment_ids) != len(set(enrollment_ids)):
        raise ValidationException(
            "Each learner can only appear once in a single class attendance summary update."
        )

    valid_enrollments = set(
        (
            await db.execute(
                select(StudentEnrollment.id).where(
                    StudentEnrollment.school_id == school_id,
                    StudentEnrollment.class_group_id == group.id,
                    StudentEnrollment.id.in_(enrollment_ids),
                    StudentEnrollment.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(valid_enrollments) != len(enrollment_ids):
        raise ValidationException(
            "One or more learners in this summary update do not belong to the selected class."
        )

    summaries: list[AttendanceSummary] = []
    for item in req.summaries:
        summary = await _upsert_manual_summary(
            db,
            school_id,
            item.student_enrollment_id,
            term.id,
            opened=item.times_school_opened,
            present=item.times_present,
            absent=item.times_absent,
            reason=req.override_reason,
            actor_id=current_user.id,
        )
        summaries.append(summary)

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="CLASS_ATTENDANCE_SUMMARY_OVERRIDDEN",
        resource_type="ClassGroup",
        resource_id=group.id,
        diff_payload={
            "academic_term_id": str(term.id),
            "summaries_updated": len(summaries),
            "reason": req.override_reason,
        },
    )
    await db.commit()
    for summary in summaries:
        await db.refresh(summary)
    return summaries


async def get_class_attendance(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    academic_term_id: UUID,
    attendance_date: date | None,
    current_user: User,
) -> dict[str, object]:
    group = await _get_class_group(db, school_id, class_group_id)
    await _require_attendance_access(db, current_user, group)
    term = await _get_term(db, school_id, academic_term_id)
    target_date = attendance_date or await _school_today(db, school_id)

    enroll_rows = (
        await db.execute(
            select(
                StudentEnrollment.id,
                Student.id,
                Student.admission_number,
                Student.first_name,
                Student.last_name,
            )
            .join(Student, Student.id == StudentEnrollment.student_id)
            .where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.class_group_id == group.id,
                StudentEnrollment.academic_session_id == term.academic_session_id,
                StudentEnrollment.enrollment_status == "ENROLLED",
                StudentEnrollment.deleted_at.is_(None),
                Student.deleted_at.is_(None),
            )
            .order_by(Student.last_name.asc(), Student.first_name.asc())
        )
    ).all()

    enrollment_ids = [row[0] for row in enroll_rows]
    record_map: dict[UUID, AttendanceRecord] = {}
    if enrollment_ids:
        records = (
            (
                await db.execute(
                    select(AttendanceRecord).where(
                        AttendanceRecord.school_id == school_id,
                        AttendanceRecord.academic_term_id == term.id,
                        AttendanceRecord.attendance_date == target_date,
                        AttendanceRecord.student_enrollment_id.in_(enrollment_ids),
                        AttendanceRecord.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        record_map = {rec.student_enrollment_id: rec for rec in records}

    items = []
    for enrollment_id, student_id, adm_no, first_name, last_name in enroll_rows:
        rec = record_map.get(enrollment_id)
        items.append(
            {
                "student_enrollment_id": enrollment_id,
                "student_id": student_id,
                "admission_number": adm_no,
                "student_name": f"{first_name} {last_name}".strip(),
                "status": rec.status if rec else None,
                "backdate_reason": rec.reason if rec else None,
            }
        )

    from app.modules.computation.models import TermReportCard

    published_exists = (
        await db.execute(
            select(TermReportCard.id)
            .where(
                TermReportCard.school_id == school_id,
                TermReportCard.academic_term_id == term.id,
                TermReportCard.publication_status == "PUBLISHED",
                TermReportCard.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None

    term_status = (
        "PUBLISHED" if published_exists else ("LOCKED" if term.is_locked_for_scoring else "DRAFT")
    )

    return {
        "class_group_id": group.id,
        "academic_term_id": term.id,
        "attendance_date": target_date,
        "is_locked_for_scoring": term.is_locked_for_scoring or published_exists,
        "term_status": term_status,
        "term_start_date": term.start_date,
        "term_end_date": term.end_date,
        "items": items,
    }


async def get_class_attendance_summary(
    db: AsyncSession,
    school_id: UUID,
    class_group_id: UUID,
    academic_term_id: UUID,
    current_user: User,
    *,
    source: str | None = None,
) -> list[dict[str, object]]:
    group = await _get_class_group(db, school_id, class_group_id)
    await _require_attendance_access(db, current_user, group)
    term = await _get_term(db, school_id, academic_term_id)

    enroll_rows = (
        await db.execute(
            select(
                StudentEnrollment.id,
                Student.id,
                Student.admission_number,
                Student.first_name,
                Student.last_name,
            )
            .join(Student, Student.id == StudentEnrollment.student_id)
            .where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.class_group_id == group.id,
                StudentEnrollment.academic_session_id == term.academic_session_id,
                StudentEnrollment.enrollment_status == "ENROLLED",
                StudentEnrollment.deleted_at.is_(None),
                Student.deleted_at.is_(None),
            )
            .order_by(Student.last_name.asc(), Student.first_name.asc())
        )
    ).all()

    enrollment_ids = [row[0] for row in enroll_rows]
    summary_map: dict[UUID, AttendanceSummary] = {}
    if enrollment_ids:
        summaries = (
            (
                await db.execute(
                    select(AttendanceSummary).where(
                        AttendanceSummary.school_id == school_id,
                        AttendanceSummary.academic_term_id == term.id,
                        AttendanceSummary.student_enrollment_id.in_(enrollment_ids),
                        AttendanceSummary.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        summary_map = {s.student_enrollment_id: s for s in summaries}

    rows = [
        {
            "student_enrollment_id": enrollment_id,
            "student_id": student_id,
            "admission_number": adm_no,
            "student_name": f"{first_name} {last_name}".strip(),
            "times_school_opened": summary_map[enrollment_id].times_school_opened
            if enrollment_id in summary_map
            else 0,
            "times_present": summary_map[enrollment_id].times_present
            if enrollment_id in summary_map
            else 0,
            "times_absent": summary_map[enrollment_id].times_absent
            if enrollment_id in summary_map
            else 0,
            "source": summary_map[enrollment_id].source if enrollment_id in summary_map else None,
            "override_reason": summary_map[enrollment_id].override_reason
            if enrollment_id in summary_map
            else None,
        }
        for enrollment_id, student_id, adm_no, first_name, last_name in enroll_rows
    ]
    if source:
        src = source.strip().upper()
        rows = [r for r in rows if (r["source"] or "").upper() == src]
    return rows


async def get_attendance_overview_summary(
    db: AsyncSession,
    school_id: UUID,
    academic_term_id: UUID,
    target_date: date | None = None,
) -> dict[str, object]:
    term = await _get_term(db, school_id, academic_term_id)
    check_date = target_date or await _school_today(db, school_id)

    distinct_dates = (
        await db.execute(
            select(func.count(func.distinct(AttendanceRecord.attendance_date))).where(
                AttendanceRecord.school_id == school_id,
                AttendanceRecord.academic_term_id == term.id,
                AttendanceRecord.deleted_at.is_(None),
            )
        )
    ).scalar_one()

    today_records = (
        await db.execute(
            select(AttendanceRecord.status, StudentEnrollment.class_group_id)
            .join(
                StudentEnrollment,
                StudentEnrollment.id == AttendanceRecord.student_enrollment_id,
            )
            .where(
                AttendanceRecord.school_id == school_id,
                AttendanceRecord.academic_term_id == term.id,
                AttendanceRecord.attendance_date == check_date,
                AttendanceRecord.deleted_at.is_(None),
            )
        )
    ).all()

    total_today = len(today_records)
    present_today = sum(1 for st, _ in today_records if st in ("PRESENT", "LATE"))
    present_today_pct = (
        float(
            ((Decimal(present_today) / Decimal(total_today)) * Decimal("100")).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        )
        if total_today
        else 0.0
    )
    classes_marked_today = len({cg_id for _, cg_id in today_records})

    class_rows = (
        await db.execute(
            select(
                ClassGroup.id,
                ClassGroup.display_name,
                Department.name,
                User.first_name,
                User.last_name,
            )
            .outerjoin(Department, Department.id == ClassGroup.department_id)
            .outerjoin(User, User.id == ClassGroup.class_teacher_id)
            .where(
                ClassGroup.school_id == school_id,
                ClassGroup.academic_session_id == term.academic_session_id,
                ClassGroup.deleted_at.is_(None),
            )
            .order_by(ClassGroup.display_name.asc())
        )
    ).all()
    total_classes = len(class_rows)

    enrollment_counts = dict(
        (
            await db.execute(
                select(StudentEnrollment.class_group_id, func.count())
                .where(
                    StudentEnrollment.school_id == school_id,
                    StudentEnrollment.academic_session_id == term.academic_session_id,
                    StudentEnrollment.enrollment_status == "ENROLLED",
                    StudentEnrollment.deleted_at.is_(None),
                )
                .group_by(StudentEnrollment.class_group_id)
            )
        ).all()
    )

    per_class_totals: dict[UUID, int] = {}
    per_class_present: dict[UUID, int] = {}
    for st, cg_id in today_records:
        per_class_totals[cg_id] = per_class_totals.get(cg_id, 0) + 1
        if st in ("PRESENT", "LATE"):
            per_class_present[cg_id] = per_class_present.get(cg_id, 0) + 1

    classes = []
    for cg_id, display_name, dept_name, t_first, t_last in class_rows:
        marked_total = per_class_totals.get(cg_id, 0)
        marked_present = per_class_present.get(cg_id, 0)
        pct = (
            float(
                ((Decimal(marked_present) / Decimal(marked_total)) * Decimal("100")).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
            )
            if marked_total
            else 0.0
        )
        teacher_name = f"{t_first} {t_last}".strip() if t_first or t_last else None
        classes.append(
            {
                "class_group_id": cg_id,
                "display_name": display_name,
                "department_name": dept_name,
                "class_teacher_name": teacher_name,
                "student_count": enrollment_counts.get(cg_id, 0),
                "marked": marked_total > 0,
                "present_count": marked_present,
                "present_pct": pct,
            }
        )

    manual_overrides = (
        await db.execute(
            select(func.count())
            .select_from(AttendanceSummary)
            .where(
                AttendanceSummary.school_id == school_id,
                AttendanceSummary.academic_term_id == term.id,
                AttendanceSummary.source == "MANUAL",
                AttendanceSummary.deleted_at.is_(None),
            )
        )
    ).scalar_one()

    return {
        "academic_term_id": term.id,
        "target_date": check_date,
        "days_school_opened": distinct_dates,
        "present_today_pct": present_today_pct,
        "classes_marked_today": classes_marked_today,
        "total_classes": total_classes,
        "manual_overrides": manual_overrides,
        "classes": classes,
    }
