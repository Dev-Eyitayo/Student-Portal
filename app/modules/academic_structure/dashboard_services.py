from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InsufficientPermissionException
from app.core.permissions import user_has_permission
from app.modules.academic_structure.models import (
    AcademicSession,
    AcademicTerm,
    ClassGroup,
    Department,
)
from app.modules.assessments.models import ScoreSheetStatus
from app.modules.audit.models import AuditLog
from app.modules.common.schemas import PageMeta
from app.modules.computation.models import TermReportCard
from app.modules.identity.models import SchoolConfiguration, User
from app.modules.students.models import AttendanceRecord, AttendanceSummary, StudentEnrollment
from app.modules.subjects.models import ClassSubject, Subject, SubjectOffering, TeacherAssignment


async def _require_admin_dashboard_access(db: AsyncSession, current_user: User) -> None:
    for code in ("structure:manage", "scores:approve", "reports:publish", "users:manage"):
        if await user_has_permission(db, current_user, code):
            return
    raise InsufficientPermissionException(permission_code="structure:manage")


async def _school_today(db: AsyncSession, school_id: UUID) -> date:
    tz_name = (
        await db.execute(
            select(SchoolConfiguration.timezone).where(SchoolConfiguration.school_id == school_id)
        )
    ).scalar_one_or_none()
    if tz_name:
        try:
            return datetime.now(UTC).astimezone(ZoneInfo(tz_name)).date()
        except ZoneInfoNotFoundError:
            pass
    return datetime.now(UTC).date()


async def _active_session_and_term(
    db: AsyncSession, school_id: UUID
) -> tuple[AcademicSession | None, AcademicTerm | None]:
    session = (
        await db.execute(
            select(AcademicSession)
            .where(
                AcademicSession.school_id == school_id,
                AcademicSession.is_current.is_(True),
                AcademicSession.deleted_at.is_(None),
            )
            .order_by(AcademicSession.start_date.desc())
        )
    ).scalar_one_or_none()

    term = None
    if session is not None:
        term = (
            await db.execute(
                select(AcademicTerm)
                .where(
                    AcademicTerm.school_id == school_id,
                    AcademicTerm.academic_session_id == session.id,
                    AcademicTerm.is_current.is_(True),
                    AcademicTerm.deleted_at.is_(None),
                )
                .order_by(AcademicTerm.term_order.asc())
            )
        ).scalar_one_or_none()
    return session, term


async def _collect_term_issues(
    db: AsyncSession,
    school_id: UUID,
    session: AcademicSession | None,
    term: AcademicTerm | None,
) -> tuple[list[dict[str, object]], int, int]:
    issues: list[dict[str, object]] = []
    if session is None:
        return issues, 0, 0

    class_groups = (
        (
            await db.execute(
                select(ClassGroup)
                .where(
                    ClassGroup.school_id == school_id,
                    ClassGroup.academic_session_id == session.id,
                    ClassGroup.deleted_at.is_(None),
                )
                .order_by(ClassGroup.display_name.asc())
            )
        )
        .scalars()
        .all()
    )

    for cg in class_groups:
        if cg.class_teacher_id is None:
            issues.append(
                {
                    "id": f"missing-ct:{cg.id}",
                    "issue_type": "MISSING_CLASS_TEACHER",
                    "title": f"{cg.display_name} — No Class Teacher assigned",
                    "subtitle": "Assign a Class Teacher to manage attendance and remarks.",
                    "action_label": "Assign Class Teacher",
                    "target_route": f"/classes-terms?class_group_id={cg.id}",
                    "class_group_id": cg.id,
                    "class_subject_id": None,
                    "academic_term_id": term.id if term else None,
                }
            )

    cs_rows = (
        await db.execute(
            select(
                ClassSubject.id,
                ClassGroup.id,
                ClassGroup.display_name,
                Subject.name,
            )
            .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
            .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
            .join(Subject, Subject.id == SubjectOffering.subject_id)
            .where(
                ClassSubject.school_id == school_id,
                ClassSubject.deleted_at.is_(None),
                ClassGroup.academic_session_id == session.id,
                ClassGroup.deleted_at.is_(None),
            )
            .order_by(ClassGroup.display_name.asc(), Subject.name.asc())
        )
    ).all()

    cs_ids = [row[0] for row in cs_rows]
    assigned_cs: set[UUID] = set()
    status_map: dict[UUID, str] = {}
    if cs_ids:
        assigned_cs = set(
            (
                await db.execute(
                    select(TeacherAssignment.class_subject_id).where(
                        TeacherAssignment.school_id == school_id,
                        TeacherAssignment.class_subject_id.in_(cs_ids),
                        TeacherAssignment.is_active.is_(True),
                        TeacherAssignment.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if term is not None:
            st_rows = (
                await db.execute(
                    select(ScoreSheetStatus.class_subject_id, ScoreSheetStatus.status).where(
                        ScoreSheetStatus.school_id == school_id,
                        ScoreSheetStatus.academic_term_id == term.id,
                        ScoreSheetStatus.class_subject_id.in_(cs_ids),
                        ScoreSheetStatus.deleted_at.is_(None),
                    )
                )
            ).all()
            status_map = {cs_id: st for cs_id, st in st_rows}

    for cs_id, cg_id, cg_name, subj_name in cs_rows:
        if cs_id not in assigned_cs:
            issues.append(
                {
                    "id": f"unassigned-subj:{cs_id}",
                    "issue_type": "UNASSIGNED_SUBJECT",
                    "title": f"{cg_name} {subj_name} — No Subject Teacher",
                    "subtitle": "Assign a teacher so scores can be entered.",
                    "action_label": "Assign Teacher",
                    "target_route": f"/classes-terms?class_group_id={cg_id}",
                    "class_group_id": cg_id,
                    "class_subject_id": cs_id,
                    "academic_term_id": term.id if term else None,
                }
            )
        elif term is not None:
            st = status_map.get(cs_id, "DRAFT")
            if st == "SUBMITTED":
                issues.append(
                    {
                        "id": f"awaiting-approval:{cs_id}",
                        "issue_type": "SCORES_AWAITING_APPROVAL",
                        "title": f"{cg_name} {subj_name} — Scores awaiting approval",
                        "subtitle": "Submitted by teacher and ready for review.",
                        "action_label": "Review scores",
                        "target_route": (
                            f"/scores-ca?tab=approvals&term_id={term.id}"
                            f"&class_group_id={cg_id}&class_subject_id={cs_id}"
                        ),
                        "class_group_id": cg_id,
                        "class_subject_id": cs_id,
                        "academic_term_id": term.id,
                    }
                )
            elif st == "DRAFT":
                issues.append(
                    {
                        "id": f"pending-scores:{cs_id}",
                        "issue_type": "PENDING_SCORE_ENTRY",
                        "title": f"{cg_name} {subj_name} — Score sheet not submitted",
                        "subtitle": "Scores are still in draft.",
                        "action_label": "View sheet",
                        "target_route": (
                            f"/scores-ca?tab=enter-scores&term_id={term.id}"
                            f"&class_group_id={cg_id}&class_subject_id={cs_id}"
                        ),
                        "class_group_id": cg_id,
                        "class_subject_id": cs_id,
                        "academic_term_id": term.id,
                    }
                )

    approved_count = sum(1 for cs_id in cs_ids if status_map.get(cs_id) == "APPROVED")
    return issues, approved_count, len(cs_ids)


async def get_admin_dashboard(
    db: AsyncSession, school_id: UUID, current_user: User
) -> dict[str, object]:
    await _require_admin_dashboard_access(db, current_user)
    session, term = await _active_session_and_term(db, school_id)

    students_enrolled = 0
    if session is not None:
        students_enrolled = (
            await db.execute(
                select(func.count(func.distinct(StudentEnrollment.student_id))).where(
                    StudentEnrollment.school_id == school_id,
                    StudentEnrollment.academic_session_id == session.id,
                    StudentEnrollment.enrollment_status == "ENROLLED",
                    StudentEnrollment.deleted_at.is_(None),
                )
            )
        ).scalar_one()

    active_staff = (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.school_id == school_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
    ).scalar_one()

    issues, approved_count, total_sheets = await _collect_term_issues(db, school_id, session, term)

    attendance_count = 0
    if term is not None:
        attendance_count = (
            await db.execute(
                select(func.count())
                .select_from(AttendanceSummary)
                .where(
                    AttendanceSummary.school_id == school_id,
                    AttendanceSummary.academic_term_id == term.id,
                    AttendanceSummary.deleted_at.is_(None),
                )
            )
        ).scalar_one()

    structure_ready = total_sheets > 0 and not any(
        i["issue_type"] in ("MISSING_CLASS_TEACHER", "UNASSIGNED_SUBJECT") for i in issues
    )
    scores_entered = total_sheets > 0 and all(
        i["issue_type"] != "PENDING_SCORE_ENTRY" for i in issues
    )
    scores_approved = total_sheets > 0 and approved_count == total_sheets
    published_cards = 0
    if term is not None:
        published_cards = (
            await db.execute(
                select(func.count())
                .select_from(TermReportCard)
                .where(
                    TermReportCard.school_id == school_id,
                    TermReportCard.academic_term_id == term.id,
                    TermReportCard.publication_status == "PUBLISHED",
                    TermReportCard.deleted_at.is_(None),
                )
            )
        ).scalar_one()

    term_published = published_cards > 0
    term_status = (
        "PUBLISHED"
        if term_published
        else ("LOCKED" if term and term.is_locked_for_scoring else ("DRAFT" if term else None))
    )

    term_workflow = [
        {
            "key": "structure",
            "label": "Classes & Subjects",
            "status": "COMPLETED" if structure_ready else ("IN_PROGRESS" if session else "PENDING"),
            "detail": f"{total_sheets} subject allocation(s)" if session else "No active session",
        },
        {
            "key": "scores_entered",
            "label": "Scores Entered",
            "status": "COMPLETED"
            if scores_entered
            else ("IN_PROGRESS" if total_sheets else "PENDING"),
            "detail": "All sheets submitted" if scores_entered else "Pending teacher submissions",
        },
        {
            "key": "scores_approved",
            "label": "Scores Approved",
            "status": "COMPLETED"
            if scores_approved
            else ("IN_PROGRESS" if approved_count > 0 else "PENDING"),
            "detail": f"{approved_count} / {total_sheets} approved",
        },
        {
            "key": "attendance",
            "label": "Attendance",
            "status": "COMPLETED"
            if attendance_count > 0 and term_published
            else ("IN_PROGRESS" if attendance_count > 0 else "PENDING"),
            "detail": f"{attendance_count} student summary record(s)",
        },
        {
            "key": "published",
            "label": "Results Published",
            "status": "COMPLETED" if term_published else "PENDING",
            "detail": term_status or "No active term",
        },
    ]

    recent_rows = (
        await db.execute(
            select(AuditLog, User.first_name, User.last_name)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .where(AuditLog.school_id == school_id)
            .order_by(AuditLog.timestamp.desc())
            .limit(8)
        )
    ).all()
    recent_activities = [
        {
            "id": log.id,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "actor_name": f"{fname} {lname}".strip() if fname or lname else None,
            "timestamp": log.timestamp,
        }
        for log, fname, lname in recent_rows
    ]

    return {
        "active_session_id": session.id if session else None,
        "active_session_name": session.name if session else None,
        "current_term_id": term.id if term else None,
        "current_term_name": term.name if term else None,
        "term_status": term_status,
        "students_enrolled": students_enrolled,
        "active_staff": active_staff,
        "score_sheets_approved": approved_count,
        "score_sheets_total": total_sheets,
        "total_issues": len(issues),
        "term_workflow": term_workflow,
        "needs_attention": issues[:6],
        "recent_activities": recent_activities,
    }


async def list_dashboard_issues(
    db: AsyncSession,
    school_id: UUID,
    current_user: User,
    *,
    issue_type: str | None = None,
    class_group_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    await _require_admin_dashboard_access(db, current_user)
    session, term = await _active_session_and_term(db, school_id)
    issues, _, _ = await _collect_term_issues(db, school_id, session, term)

    if issue_type:
        allowed_types = {t.strip().upper() for t in issue_type.split(",") if t.strip()}
        if allowed_types:
            issues = [i for i in issues if i["issue_type"] in allowed_types]
    if class_group_id is not None:
        issues = [i for i in issues if i["class_group_id"] == class_group_id]

    total = len(issues)
    offset = (page - 1) * page_size
    paged = issues[offset : offset + page_size]
    meta = PageMeta.build(page=page, page_size=page_size, total=total)
    return paged, meta


async def get_teacher_dashboard(
    db: AsyncSession, school_id: UUID, current_user: User
) -> dict[str, object]:
    session, term = await _active_session_and_term(db, school_id)
    today = await _school_today(db, school_id)

    cg_stmt = select(ClassGroup).where(
        ClassGroup.school_id == school_id,
        ClassGroup.class_teacher_id == current_user.id,
        ClassGroup.deleted_at.is_(None),
    )
    if session is not None:
        cg_stmt = cg_stmt.where(ClassGroup.academic_session_id == session.id)
    my_groups = (await db.execute(cg_stmt.order_by(ClassGroup.display_name.asc()))).scalars().all()

    my_classes: list[dict[str, object]] = []
    if my_groups:
        dept_ids = list({cg.department_id for cg in my_groups if cg.department_id is not None})
        dept_map: dict[UUID, str] = {}
        if dept_ids:
            dept_map = dict(
                (
                    await db.execute(
                        select(Department.id, Department.name).where(Department.id.in_(dept_ids))
                    )
                ).all()
            )
        cg_ids = [cg.id for cg in my_groups]
        enroll_rows = (
            await db.execute(
                select(StudentEnrollment.id, StudentEnrollment.class_group_id).where(
                    StudentEnrollment.school_id == school_id,
                    StudentEnrollment.class_group_id.in_(cg_ids),
                    StudentEnrollment.enrollment_status == "ENROLLED",
                    StudentEnrollment.deleted_at.is_(None),
                )
            )
        ).all()
        enrolls_by_cg: dict[UUID, list[UUID]] = {}
        all_enroll_ids: list[UUID] = []
        for eid, cg_id in enroll_rows:
            enrolls_by_cg.setdefault(cg_id, []).append(eid)
            all_enroll_ids.append(eid)

        marked_by_eid: set[UUID] = set()
        if term is not None and all_enroll_ids:
            marked_by_eid = set(
                (
                    await db.execute(
                        select(AttendanceRecord.student_enrollment_id).where(
                            AttendanceRecord.school_id == school_id,
                            AttendanceRecord.academic_term_id == term.id,
                            AttendanceRecord.attendance_date == today,
                            AttendanceRecord.student_enrollment_id.in_(all_enroll_ids),
                            AttendanceRecord.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )

        for cg in my_groups:
            cg_eids = enrolls_by_cg.get(cg.id, [])
            marked_count = sum(1 for eid in cg_eids if eid in marked_by_eid)
            my_classes.append(
                {
                    "class_group_id": cg.id,
                    "display_name": cg.display_name,
                    "department_name": dept_map.get(cg.department_id) if cg.department_id else None,
                    "student_count": len(cg_eids),
                    "today_attendance_marked": marked_count > 0,
                    "marked_count": marked_count,
                }
            )

    ta_stmt = (
        select(
            ClassSubject.id,
            ClassGroup.id,
            ClassGroup.display_name,
            Department.name,
            Subject.id,
            Subject.name,
            Subject.code,
        )
        .select_from(TeacherAssignment)
        .join(ClassSubject, ClassSubject.id == TeacherAssignment.class_subject_id)
        .join(ClassGroup, ClassGroup.id == ClassSubject.class_group_id)
        .outerjoin(Department, Department.id == ClassGroup.department_id)
        .join(SubjectOffering, SubjectOffering.id == ClassSubject.subject_offering_id)
        .join(Subject, Subject.id == SubjectOffering.subject_id)
        .where(
            TeacherAssignment.school_id == school_id,
            TeacherAssignment.teacher_id == current_user.id,
            TeacherAssignment.is_active.is_(True),
            TeacherAssignment.deleted_at.is_(None),
            ClassSubject.deleted_at.is_(None),
            ClassGroup.deleted_at.is_(None),
        )
        .order_by(ClassGroup.display_name.asc(), Subject.name.asc())
    )
    if session is not None:
        ta_stmt = ta_stmt.where(ClassGroup.academic_session_id == session.id)
    ta_rows = (await db.execute(ta_stmt)).all()

    cs_ids = [row[0] for row in ta_rows]
    status_map: dict[UUID, str] = {}
    if term is not None and cs_ids:
        st_rows = (
            await db.execute(
                select(ScoreSheetStatus.class_subject_id, ScoreSheetStatus.status).where(
                    ScoreSheetStatus.school_id == school_id,
                    ScoreSheetStatus.academic_term_id == term.id,
                    ScoreSheetStatus.class_subject_id.in_(cs_ids),
                    ScoreSheetStatus.deleted_at.is_(None),
                )
            )
        ).all()
        status_map = {cs_id: st for cs_id, st in st_rows}

    teaching_subjects = []
    for cs_id, cg_id, cg_name, dept_name, s_id, s_name, s_code in ta_rows:
        st = status_map.get(cs_id, "DRAFT")
        term_q = f"&term_id={term.id}" if term else ""
        teaching_subjects.append(
            {
                "class_subject_id": cs_id,
                "class_group_id": cg_id,
                "class_group_name": cg_name,
                "department_name": dept_name,
                "subject_id": s_id,
                "subject_name": s_name,
                "subject_code": s_code,
                "approval_status": st,
                "target_route": f"/scores-ca?tab=enter-scores{term_q}&class_subject_id={cs_id}",
            }
        )

    pending_score_sheets = sum(
        1 for item in teaching_subjects if item["approval_status"] == "DRAFT"
    )

    return {
        "current_term_id": term.id if term else None,
        "current_term_name": term.name if term else None,
        "my_classes": my_classes,
        "subjects_count": len(teaching_subjects),
        "pending_score_sheets": pending_score_sheets,
        "teaching_subjects": teaching_subjects,
    }
