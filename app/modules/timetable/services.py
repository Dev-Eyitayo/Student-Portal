import re
from datetime import UTC, datetime, time
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from app.modules.academic_structure.models import (
    AcademicLevel,
    AcademicSession,
    AcademicTerm,
    ClassGroup,
)
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.identity.models import User
from app.modules.students.models import StudentEnrollment
from app.modules.subjects.models import (
    ClassSubject,
    StudentSubjectRegistration,
    Subject,
    SubjectOffering,
    TeacherAssignment,
)
from app.modules.timetable.engine import (
    ClassSubjectSpec,
    PinnedEntrySpec,
    RoomSpec,
    SlotSpec,
    SolvedEntry,
    SyncGroupSpec,
    TeacherConstraintSpec,
    TimetableSolveResult,
    solve_timetable,
    validate_manual_move,
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
from app.modules.timetable.schemas import (
    ApplyRelaxationRequest,
    AssistantActionPreview,
    AvailabilityType,
    BellScheduleCreateRequest,
    BellScheduleResponse,
    BellScheduleSlotsReplaceRequest,
    BellScheduleUpdateRequest,
    RelaxationSuggestion,
    RoomCreateRequest,
    RoomResponse,
    RoomUpdateRequest,
    SlotType,
    SubjectSchedulingRuleResponse,
    SubjectSchedulingRuleUpsertRequest,
    SyncGroupCreateRequest,
    SyncGroupResponse,
    SyncGroupUpdateRequest,
    SyncType,
    TeacherAvailabilityResponse,
    TeacherAvailabilityUpsertRequest,
    TimetableCreateRequest,
    TimetableEntryMoveValidateRequest,
    TimetableEntryMoveValidateResponse,
    TimetableEntryUpdateRequest,
    TimetableEntryView,
    TimetableGridResponse,
    TimetableMasterGridResponse,
    TimetableResponse,
    TimetableSlotCreate,
    TimetableSlotResponse,
)


def _now_utc() -> datetime:
    return datetime.now(UTC)


async def _get_bell_schedule_or_404(
    db: AsyncSession, school_id: UUID, schedule_id: UUID
) -> BellSchedule:
    schedule = (
        await db.execute(
            select(BellSchedule)
            .options(selectinload(BellSchedule.slots))
            .where(
                BellSchedule.id == schedule_id,
                BellSchedule.school_id == school_id,
                BellSchedule.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if schedule is None:
        raise ResourceNotFoundException("bell schedule", schedule_id)
    return schedule


def _serialize_bell_schedule(schedule: BellSchedule) -> BellScheduleResponse:
    active_slots = sorted(
        [s for s in schedule.slots if s.deleted_at is None],
        key=lambda s: (s.day_of_week, s.period_order),
    )
    return BellScheduleResponse(
        id=schedule.id,
        school_id=schedule.school_id,
        academic_session_id=schedule.academic_session_id,
        level_category=schedule.level_category,
        academic_level_id=schedule.academic_level_id,
        name=schedule.name,
        is_active=schedule.is_active,
        slots=[TimetableSlotResponse.model_validate(s) for s in active_slots],
    )


async def list_bell_schedules(
    db: AsyncSession, school_id: UUID, academic_session_id: UUID | None = None
) -> list[BellScheduleResponse]:
    stmt = (
        select(BellSchedule)
        .options(selectinload(BellSchedule.slots))
        .where(BellSchedule.school_id == school_id, BellSchedule.deleted_at.is_(None))
    )
    if academic_session_id is not None:
        stmt = stmt.where(BellSchedule.academic_session_id == academic_session_id)
    stmt = stmt.order_by(BellSchedule.level_category, BellSchedule.name)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize_bell_schedule(row) for row in rows]


async def create_bell_schedule(
    db: AsyncSession,
    school_id: UUID,
    req: BellScheduleCreateRequest,
    audit: AuditContext,
) -> BellScheduleResponse:
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

    dup_stmt = select(BellSchedule).where(
        BellSchedule.school_id == school_id,
        BellSchedule.academic_session_id == req.academic_session_id,
        BellSchedule.deleted_at.is_(None),
    )
    if req.academic_level_id is not None:
        dup_stmt = dup_stmt.where(BellSchedule.academic_level_id == req.academic_level_id)
    else:
        dup_stmt = dup_stmt.where(
            BellSchedule.level_category == req.level_category.value,
            BellSchedule.academic_level_id.is_(None),
        )
    if (await db.execute(dup_stmt)).scalar_one_or_none() is not None:
        raise ConflictException(
            message="A bell schedule already exists for this session and level/category.",
            code="BELL_SCHEDULE_DUPLICATE",
        )

    schedule = BellSchedule(
        school_id=school_id,
        academic_session_id=req.academic_session_id,
        level_category=req.level_category.value,
        academic_level_id=req.academic_level_id,
        name=req.name.strip(),
        is_active=True,
    )
    db.add(schedule)
    await db.flush()

    for slot_in in req.slots:
        db.add(
            TimetableSlot(
                school_id=school_id,
                bell_schedule_id=schedule.id,
                day_of_week=slot_in.day_of_week,
                period_order=slot_in.period_order,
                label=slot_in.label.strip(),
                start_time=slot_in.start_time,
                end_time=slot_in.end_time,
                slot_type=slot_in.slot_type.value,
            )
        )

    record_audit_log(
        db,
        audit,
        action="BELL_SCHEDULE_CREATED",
        resource_type="BellSchedule",
        resource_id=schedule.id,
        diff_payload={"name": schedule.name, "level_category": schedule.level_category},
    )
    await db.commit()
    return _serialize_bell_schedule(await _get_bell_schedule_or_404(db, school_id, schedule.id))


async def get_bell_schedule(
    db: AsyncSession, school_id: UUID, schedule_id: UUID
) -> BellScheduleResponse:
    return _serialize_bell_schedule(await _get_bell_schedule_or_404(db, school_id, schedule_id))


async def update_bell_schedule(
    db: AsyncSession,
    school_id: UUID,
    schedule_id: UUID,
    req: BellScheduleUpdateRequest,
    audit: AuditContext,
) -> BellScheduleResponse:
    schedule = await _get_bell_schedule_or_404(db, school_id, schedule_id)
    if req.name is not None:
        schedule.name = req.name.strip()
    if req.is_active is not None:
        schedule.is_active = req.is_active
    record_audit_log(
        db,
        audit,
        action="BELL_SCHEDULE_UPDATED",
        resource_type="BellSchedule",
        resource_id=schedule.id,
        diff_payload={"name": schedule.name, "is_active": schedule.is_active},
    )
    await db.commit()
    return _serialize_bell_schedule(await _get_bell_schedule_or_404(db, school_id, schedule.id))


async def delete_bell_schedule(
    db: AsyncSession, school_id: UUID, schedule_id: UUID, audit: AuditContext
) -> None:
    schedule = await _get_bell_schedule_or_404(db, school_id, schedule_id)
    in_use = (
        await db.execute(
            select(TimetableEntry.id)
            .join(TimetableSlot, TimetableSlot.id == TimetableEntry.timetable_slot_id)
            .where(
                TimetableSlot.bell_schedule_id == schedule.id,
                TimetableEntry.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if in_use is not None:
        raise ConflictException(
            message="This bell schedule is already referenced by timetable entries and cannot be deleted.",
            code="BELL_SCHEDULE_IN_USE",
        )
    now = _now_utc()
    schedule.deleted_at = now
    for s in schedule.slots:
        if s.deleted_at is None:
            s.deleted_at = now
    record_audit_log(
        db,
        audit,
        action="BELL_SCHEDULE_DELETED",
        resource_type="BellSchedule",
        resource_id=schedule.id,
        diff_payload={"name": schedule.name},
    )
    await db.commit()


async def replace_bell_schedule_slots(
    db: AsyncSession,
    school_id: UUID,
    schedule_id: UUID,
    req: BellScheduleSlotsReplaceRequest,
    audit: AuditContext,
) -> BellScheduleResponse:
    schedule = await _get_bell_schedule_or_404(db, school_id, schedule_id)
    now = _now_utc()
    for existing in schedule.slots:
        if existing.deleted_at is None:
            existing.deleted_at = now
    await db.flush()

    for slot_in in req.slots:
        db.add(
            TimetableSlot(
                school_id=school_id,
                bell_schedule_id=schedule.id,
                day_of_week=slot_in.day_of_week,
                period_order=slot_in.period_order,
                label=slot_in.label.strip(),
                start_time=slot_in.start_time,
                end_time=slot_in.end_time,
                slot_type=slot_in.slot_type.value,
            )
        )

    record_audit_log(
        db,
        audit,
        action="BELL_SCHEDULE_SLOTS_REPLACED",
        resource_type="BellSchedule",
        resource_id=schedule.id,
        diff_payload={"slot_count": len(req.slots)},
    )
    await db.commit()
    return _serialize_bell_schedule(await _get_bell_schedule_or_404(db, school_id, schedule.id))


# ---------------------------------------------------------------------------
# Rooms (Opt-in shared bottleneck facilities)
# ---------------------------------------------------------------------------


async def list_rooms(db: AsyncSession, school_id: UUID) -> list[RoomResponse]:
    rows = (
        (
            await db.execute(
                select(Room)
                .where(Room.school_id == school_id, Room.deleted_at.is_(None))
                .order_by(Room.name)
            )
        )
        .scalars()
        .all()
    )
    return [RoomResponse.model_validate(r) for r in rows]


async def create_room(
    db: AsyncSession, school_id: UUID, req: RoomCreateRequest, audit: AuditContext
) -> RoomResponse:
    code_upper = req.code.strip().upper()
    existing = (
        await db.execute(
            select(Room).where(
                Room.school_id == school_id, Room.code == code_upper, Room.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictException(
            message=f"A facility with code '{code_upper}' already exists.",
            code="ROOM_CODE_DUPLICATE",
        )
    room = Room(
        school_id=school_id,
        name=req.name.strip(),
        code=code_upper,
        room_type=req.room_type.value,
        capacity=req.capacity,
    )
    db.add(room)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="ROOM_CREATED",
        resource_type="Room",
        resource_id=room.id,
        diff_payload={"code": room.code, "name": room.name},
    )
    await db.commit()
    return RoomResponse.model_validate(room)


async def update_room(
    db: AsyncSession,
    school_id: UUID,
    room_id: UUID,
    req: RoomUpdateRequest,
    audit: AuditContext,
) -> RoomResponse:
    room = (
        await db.execute(
            select(Room).where(
                Room.id == room_id, Room.school_id == school_id, Room.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if room is None:
        raise ResourceNotFoundException("facility", room_id)
    if req.name is not None:
        room.name = req.name.strip()
    if req.code is not None:
        room.code = req.code.strip().upper()
    if req.room_type is not None:
        room.room_type = req.room_type.value
    if req.capacity is not None:
        room.capacity = req.capacity
    record_audit_log(
        db,
        audit,
        action="ROOM_UPDATED",
        resource_type="Room",
        resource_id=room.id,
        diff_payload={"code": room.code, "name": room.name},
    )
    await db.commit()
    return RoomResponse.model_validate(room)


async def delete_room(
    db: AsyncSession, school_id: UUID, room_id: UUID, audit: AuditContext
) -> None:
    room = (
        await db.execute(
            select(Room).where(
                Room.id == room_id, Room.school_id == school_id, Room.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if room is None:
        raise ResourceNotFoundException("facility", room_id)
    room.deleted_at = _now_utc()
    record_audit_log(
        db,
        audit,
        action="ROOM_DELETED",
        resource_type="Room",
        resource_id=room.id,
        diff_payload={"code": room.code},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Subject Scheduling Rules
# ---------------------------------------------------------------------------


async def list_scheduling_rules(
    db: AsyncSession, school_id: UUID, academic_session_id: UUID | None = None
) -> list[SubjectSchedulingRuleResponse]:
    stmt = select(SubjectSchedulingRule).where(
        SubjectSchedulingRule.school_id == school_id,
        SubjectSchedulingRule.deleted_at.is_(None),
    )
    if academic_session_id is not None:
        stmt = stmt.where(SubjectSchedulingRule.academic_session_id == academic_session_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [SubjectSchedulingRuleResponse.model_validate(r) for r in rows]


async def upsert_scheduling_rule(
    db: AsyncSession,
    school_id: UUID,
    req: SubjectSchedulingRuleUpsertRequest,
    audit: AuditContext,
) -> SubjectSchedulingRuleResponse:
    if req.department_id is not None and req.academic_level_id is None:
        raise ValidationException(
            "Please select an academic level when specifying a department-level rule."
        )
    stmt = select(SubjectSchedulingRule).where(
        SubjectSchedulingRule.school_id == school_id,
        SubjectSchedulingRule.academic_session_id == req.academic_session_id,
        SubjectSchedulingRule.subject_id == req.subject_id,
        SubjectSchedulingRule.deleted_at.is_(None),
    )
    if req.academic_level_id is None:
        stmt = stmt.where(SubjectSchedulingRule.academic_level_id.is_(None))
    else:
        stmt = stmt.where(SubjectSchedulingRule.academic_level_id == req.academic_level_id)
    if req.department_id is None:
        stmt = stmt.where(SubjectSchedulingRule.department_id.is_(None))
    else:
        stmt = stmt.where(SubjectSchedulingRule.department_id == req.department_id)

    rule = (await db.execute(stmt)).scalar_one_or_none()
    if rule is None:
        rule = SubjectSchedulingRule(
            school_id=school_id,
            academic_session_id=req.academic_session_id,
            academic_level_id=req.academic_level_id,
            department_id=req.department_id,
            subject_id=req.subject_id,
        )
        db.add(rule)

    rule.periods_per_week = req.periods_per_week
    rule.double_periods_per_week = req.double_periods_per_week
    rule.allow_double_period_split = req.allow_double_period_split
    rule.max_periods_per_day = req.max_periods_per_day
    rule.prefer_morning = req.prefer_morning
    rule.preferred_room_id = req.preferred_room_id
    rule.room_only_for_double_periods = req.room_only_for_double_periods
    await db.flush()

    record_audit_log(
        db,
        audit,
        action="SCHEDULING_RULE_UPSERTED",
        resource_type="SubjectSchedulingRule",
        resource_id=rule.id,
        diff_payload={
            "subject_id": str(rule.subject_id),
            "periods_per_week": rule.periods_per_week,
            "double_periods_per_week": rule.double_periods_per_week,
        },
    )
    await db.commit()
    return SubjectSchedulingRuleResponse.model_validate(rule)


async def delete_scheduling_rule(
    db: AsyncSession, school_id: UUID, rule_id: UUID, audit: AuditContext
) -> None:
    rule = (
        await db.execute(
            select(SubjectSchedulingRule).where(
                SubjectSchedulingRule.id == rule_id,
                SubjectSchedulingRule.school_id == school_id,
                SubjectSchedulingRule.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if rule is None:
        raise ResourceNotFoundException("scheduling rule", rule_id)
    rule.deleted_at = _now_utc()
    record_audit_log(
        db,
        audit,
        action="SCHEDULING_RULE_DELETED",
        resource_type="SubjectSchedulingRule",
        resource_id=rule.id,
        diff_payload={},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Sync Groups (JOINT_CLASS / PARALLEL_SPLIT)
# ---------------------------------------------------------------------------


def _serialize_sync_group(sg: TimetableSyncGroup) -> SyncGroupResponse:
    return SyncGroupResponse(
        id=sg.id,
        school_id=sg.school_id,
        academic_session_id=sg.academic_session_id,
        name=sg.name,
        sync_type=sg.sync_type,
        class_subject_ids=[item.class_subject_id for item in sg.items if item.deleted_at is None],
    )


async def list_sync_groups(
    db: AsyncSession, school_id: UUID, academic_session_id: UUID | None = None
) -> list[SyncGroupResponse]:
    stmt = (
        select(TimetableSyncGroup)
        .options(selectinload(TimetableSyncGroup.items))
        .where(
            TimetableSyncGroup.school_id == school_id,
            TimetableSyncGroup.deleted_at.is_(None),
        )
    )
    if academic_session_id is not None:
        stmt = stmt.where(TimetableSyncGroup.academic_session_id == academic_session_id)
    rows = (await db.execute(stmt.order_by(TimetableSyncGroup.name))).scalars().all()
    return [_serialize_sync_group(r) for r in rows]


async def create_sync_group(
    db: AsyncSession, school_id: UUID, req: SyncGroupCreateRequest, audit: AuditContext
) -> SyncGroupResponse:
    sg = TimetableSyncGroup(
        school_id=school_id,
        academic_session_id=req.academic_session_id,
        name=req.name.strip(),
        sync_type=req.sync_type.value,
    )
    db.add(sg)
    await db.flush()
    for csid in dict.fromkeys(req.class_subject_ids):
        db.add(
            TimetableSyncGroupItem(school_id=school_id, sync_group_id=sg.id, class_subject_id=csid)
        )
    record_audit_log(
        db,
        audit,
        action="SYNC_GROUP_CREATED",
        resource_type="TimetableSyncGroup",
        resource_id=sg.id,
        diff_payload={"name": sg.name, "sync_type": sg.sync_type},
    )
    await db.commit()
    refreshed = (
        await db.execute(
            select(TimetableSyncGroup)
            .options(selectinload(TimetableSyncGroup.items))
            .where(TimetableSyncGroup.id == sg.id)
        )
    ).scalar_one()
    return _serialize_sync_group(refreshed)


async def update_sync_group(
    db: AsyncSession,
    school_id: UUID,
    sync_group_id: UUID,
    req: SyncGroupUpdateRequest,
    audit: AuditContext,
) -> SyncGroupResponse:
    sg = (
        await db.execute(
            select(TimetableSyncGroup)
            .options(selectinload(TimetableSyncGroup.items))
            .where(
                TimetableSyncGroup.id == sync_group_id,
                TimetableSyncGroup.school_id == school_id,
                TimetableSyncGroup.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if sg is None:
        raise ResourceNotFoundException("sync group", sync_group_id)
    if req.name is not None:
        sg.name = req.name.strip()
    if req.class_subject_ids is not None:
        now = _now_utc()
        for item in sg.items:
            if item.deleted_at is None:
                item.deleted_at = now
        await db.flush()
        for csid in dict.fromkeys(req.class_subject_ids):
            db.add(
                TimetableSyncGroupItem(
                    school_id=school_id, sync_group_id=sg.id, class_subject_id=csid
                )
            )
    record_audit_log(
        db,
        audit,
        action="SYNC_GROUP_UPDATED",
        resource_type="TimetableSyncGroup",
        resource_id=sg.id,
        diff_payload={"name": sg.name},
    )
    await db.commit()
    refreshed = (
        await db.execute(
            select(TimetableSyncGroup)
            .options(selectinload(TimetableSyncGroup.items))
            .where(TimetableSyncGroup.id == sg.id)
        )
    ).scalar_one()
    return _serialize_sync_group(refreshed)


async def delete_sync_group(
    db: AsyncSession, school_id: UUID, sync_group_id: UUID, audit: AuditContext
) -> None:
    sg = (
        await db.execute(
            select(TimetableSyncGroup)
            .options(selectinload(TimetableSyncGroup.items))
            .where(
                TimetableSyncGroup.id == sync_group_id,
                TimetableSyncGroup.school_id == school_id,
                TimetableSyncGroup.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if sg is None:
        raise ResourceNotFoundException("sync group", sync_group_id)
    now = _now_utc()
    sg.deleted_at = now
    for item in sg.items:
        if item.deleted_at is None:
            item.deleted_at = now
    record_audit_log(
        db,
        audit,
        action="SYNC_GROUP_DELETED",
        resource_type="TimetableSyncGroup",
        resource_id=sg.id,
        diff_payload={"name": sg.name},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Teacher Availabilities
# ---------------------------------------------------------------------------


async def list_teacher_availabilities(
    db: AsyncSession, school_id: UUID, academic_session_id: UUID | None = None
) -> list[TeacherAvailabilityResponse]:
    stmt = select(TeacherAvailability).where(
        TeacherAvailability.school_id == school_id,
        TeacherAvailability.deleted_at.is_(None),
    )
    if academic_session_id is not None:
        stmt = stmt.where(TeacherAvailability.academic_session_id == academic_session_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [TeacherAvailabilityResponse.model_validate(r) for r in rows]


async def upsert_teacher_availability(
    db: AsyncSession,
    school_id: UUID,
    req: TeacherAvailabilityUpsertRequest,
    audit: AuditContext,
) -> TeacherAvailabilityResponse:
    avail = TeacherAvailability(
        school_id=school_id,
        academic_session_id=req.academic_session_id,
        teacher_id=req.teacher_id,
        day_of_week=req.day_of_week,
        start_time=req.start_time,
        end_time=req.end_time,
        availability_type=req.availability_type.value,
        max_periods_per_day=req.max_periods_per_day,
        max_periods_per_week=req.max_periods_per_week,
        max_consecutive_periods=req.max_consecutive_periods,
        min_gap_minutes_between_categories=req.min_gap_minutes_between_categories,
    )
    db.add(avail)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="TEACHER_AVAILABILITY_CREATED",
        resource_type="TeacherAvailability",
        resource_id=avail.id,
        diff_payload={"teacher_id": str(avail.teacher_id), "type": avail.availability_type},
    )
    await db.commit()
    return TeacherAvailabilityResponse.model_validate(avail)


async def delete_teacher_availability(
    db: AsyncSession, school_id: UUID, availability_id: UUID, audit: AuditContext
) -> None:
    row = (
        await db.execute(
            select(TeacherAvailability).where(
                TeacherAvailability.id == availability_id,
                TeacherAvailability.school_id == school_id,
                TeacherAvailability.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ResourceNotFoundException("teacher availability", availability_id)
    row.deleted_at = _now_utc()
    record_audit_log(
        db,
        audit,
        action="TEACHER_AVAILABILITY_DELETED",
        resource_type="TeacherAvailability",
        resource_id=row.id,
        diff_payload={},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Timetables (Schedules), Generation, Manual Edits, Publish, Clone & Views
# ---------------------------------------------------------------------------


async def _get_timetable_or_404(db: AsyncSession, school_id: UUID, timetable_id: UUID) -> Timetable:
    row = (
        await db.execute(
            select(Timetable).where(
                Timetable.id == timetable_id,
                Timetable.school_id == school_id,
                Timetable.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ResourceNotFoundException("timetable", timetable_id)
    return row


async def list_timetables(
    db: AsyncSession, school_id: UUID, academic_term_id: UUID | None = None
) -> list[TimetableResponse]:
    stmt = select(Timetable).where(Timetable.school_id == school_id, Timetable.deleted_at.is_(None))
    if academic_term_id is not None:
        stmt = stmt.where(Timetable.academic_term_id == academic_term_id)
    rows = (await db.execute(stmt.order_by(Timetable.created_at.desc()))).scalars().all()
    return [TimetableResponse.model_validate(r) for r in rows]


async def create_timetable(
    db: AsyncSession, school_id: UUID, req: TimetableCreateRequest, audit: AuditContext
) -> TimetableResponse:
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

    tt = Timetable(
        school_id=school_id,
        academic_term_id=req.academic_term_id,
        name=req.name.strip(),
        status="DRAFT",
        generation_status="IDLE",
        version=1,
    )
    db.add(tt)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="TIMETABLE_CREATED",
        resource_type="Timetable",
        resource_id=tt.id,
        diff_payload={"name": tt.name, "academic_term_id": str(tt.academic_term_id)},
    )
    await db.commit()
    return TimetableResponse.model_validate(tt)


async def get_timetable(db: AsyncSession, school_id: UUID, timetable_id: UUID) -> TimetableResponse:
    return TimetableResponse.model_validate(
        await _get_timetable_or_404(db, school_id, timetable_id)
    )


async def delete_timetable(
    db: AsyncSession, school_id: UUID, timetable_id: UUID, audit: AuditContext
) -> None:
    tt = await _get_timetable_or_404(db, school_id, timetable_id)
    if tt.status == "PUBLISHED":
        raise ConflictException(
            message="Published timetables cannot be deleted. Archive or clone it into a new draft instead.",
            code="TIMETABLE_PUBLISHED_LOCKED",
        )
    tt.deleted_at = _now_utc()
    record_audit_log(
        db,
        audit,
        action="TIMETABLE_DELETED",
        resource_type="Timetable",
        resource_id=tt.id,
        diff_payload={"name": tt.name},
    )
    await db.commit()


def _resolve_rule_for_class_subject(
    rules: list[SubjectSchedulingRule],
    *,
    level_id: UUID,
    department_id: UUID | None,
    subject_id: UUID,
) -> SubjectSchedulingRule | None:
    exact = next(
        (
            r
            for r in rules
            if r.subject_id == subject_id
            and r.academic_level_id == level_id
            and r.department_id == department_id
        ),
        None,
    )
    if exact:
        return exact
    level_only = next(
        (
            r
            for r in rules
            if r.subject_id == subject_id
            and r.academic_level_id == level_id
            and r.department_id is None
        ),
        None,
    )
    if level_only:
        return level_only
    return next(
        (
            r
            for r in rules
            if r.subject_id == subject_id
            and r.academic_level_id is None
            and r.department_id is None
        ),
        None,
    )


async def _load_solver_context(db: AsyncSession, school_id: UUID, tt: Timetable) -> dict[str, Any]:
    term = (
        await db.execute(select(AcademicTerm).where(AcademicTerm.id == tt.academic_term_id))
    ).scalar_one()
    session_id = term.academic_session_id

    schedules = (
        (
            await db.execute(
                select(BellSchedule)
                .options(selectinload(BellSchedule.slots))
                .where(
                    BellSchedule.school_id == school_id,
                    BellSchedule.academic_session_id == session_id,
                    BellSchedule.is_active.is_(True),
                    BellSchedule.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    slot_specs: list[SlotSpec] = []
    schedule_by_level: dict[UUID, BellSchedule] = {}
    schedule_by_cat: dict[str, BellSchedule] = {}
    for sched in schedules:
        if sched.academic_level_id is not None:
            schedule_by_level[sched.academic_level_id] = sched
        else:
            schedule_by_cat[sched.level_category] = sched
        for s in sched.slots:
            if s.deleted_at is None:
                slot_specs.append(
                    SlotSpec(
                        id=s.id,
                        bell_schedule_id=s.bell_schedule_id,
                        day_of_week=s.day_of_week,
                        period_order=s.period_order,
                        label=s.label,
                        start_time=s.start_time,
                        end_time=s.end_time,
                        slot_type=s.slot_type,
                    )
                )

    levels = {
        lvl.id: lvl
        for lvl in (
            await db.execute(
                select(AcademicLevel).where(
                    AcademicLevel.school_id == school_id, AcademicLevel.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    }
    class_groups = {
        cg.id: cg
        for cg in (
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
    }
    offerings = {
        off.id: off
        for off in (
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
    }
    subjects = {
        sub.id: sub
        for sub in (
            await db.execute(
                select(Subject).where(Subject.school_id == school_id, Subject.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    }
    rules = list(
        (
            await db.execute(
                select(SubjectSchedulingRule).where(
                    SubjectSchedulingRule.school_id == school_id,
                    SubjectSchedulingRule.academic_session_id == session_id,
                    SubjectSchedulingRule.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    rooms = {
        r.id: RoomSpec(id=r.id, name=r.name, code=r.code, capacity=r.capacity)
        for r in (
            await db.execute(
                select(Room).where(Room.school_id == school_id, Room.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    }
    class_subjects_rows = (
        (
            await db.execute(
                select(ClassSubject).where(
                    ClassSubject.school_id == school_id, ClassSubject.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )

    tas = (
        (
            await db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.is_active.is_(True),
                    TeacherAssignment.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    teachers_by_cs: dict[UUID, list[UUID]] = {}
    for ta in tas:
        teachers_by_cs.setdefault(ta.class_subject_id, []).append(ta.teacher_id)

    enroll_counts_rows = (
        await db.execute(
            select(StudentEnrollment.class_group_id).where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.academic_session_id == session_id,
                StudentEnrollment.enrollment_status.in_(["ENROLLED", "REPEATING"]),
                StudentEnrollment.deleted_at.is_(None),
            )
        )
    ).all()
    cg_enroll_count: dict[UUID, int] = {}
    for (cg_id,) in enroll_counts_rows:
        cg_enroll_count[cg_id] = cg_enroll_count.get(cg_id, 0) + 1

    cs_specs: list[ClassSubjectSpec] = []
    missing_schedule_categories: set[str] = set()
    for cs in class_subjects_rows:
        cg = class_groups.get(cs.class_group_id)
        off = offerings.get(cs.subject_offering_id)
        if cg is None or off is None:
            continue
        lvl = levels.get(cg.academic_level_id)
        subj = subjects.get(off.subject_id)
        if lvl is None or subj is None:
            continue
        sched = schedule_by_level.get(lvl.id) or schedule_by_cat.get(lvl.category)
        if sched is None:
            missing_schedule_categories.add(lvl.category)
            continue
        rule = _resolve_rule_for_class_subject(
            rules,
            level_id=lvl.id,
            department_id=cg.department_id,
            subject_id=subj.id,
        )
        default_periods = 4 if subj.code in ("ENG", "MTH") else 2
        default_doubles = 1 if subj.code in ("ENG", "MTH", "PHY", "CHM", "BIO") else 0
        cs_specs.append(
            ClassSubjectSpec(
                class_subject_id=cs.id,
                class_group_id=cg.id,
                class_group_name=cg.display_name,
                level_category=lvl.category,
                bell_schedule_id=sched.id,
                subject_id=subj.id,
                subject_code=subj.code,
                subject_name=subj.name,
                teacher_ids=tuple(sorted(set(teachers_by_cs.get(cs.id, [])))),
                periods_per_week=rule.periods_per_week if rule else default_periods,
                double_periods_per_week=rule.double_periods_per_week if rule else default_doubles,
                allow_double_period_split=rule.allow_double_period_split if rule else False,
                max_periods_per_day=rule.max_periods_per_day if rule else 2,
                prefer_morning=(
                    rule.prefer_morning
                    if rule
                    else subj.code in ("ENG", "MTH", "PHY", "CHM", "BIO")
                ),
                preferred_room_id=rule.preferred_room_id if rule else None,
                room_only_for_double_periods=(rule.room_only_for_double_periods if rule else True),
                enrollment_count=cg_enroll_count.get(cg.id, 0),
            )
        )

    sync_rows = (
        (
            await db.execute(
                select(TimetableSyncGroup)
                .options(selectinload(TimetableSyncGroup.items))
                .where(
                    TimetableSyncGroup.school_id == school_id,
                    TimetableSyncGroup.academic_session_id == session_id,
                    TimetableSyncGroup.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    sync_specs = [
        SyncGroupSpec(
            id=sg.id,
            name=sg.name,
            sync_type=sg.sync_type,
            class_subject_ids=tuple(i.class_subject_id for i in sg.items if i.deleted_at is None),
        )
        for sg in sync_rows
    ]

    users = {
        u.id: u
        for u in (
            await db.execute(
                select(User).where(User.school_id == school_id, User.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    }
    avail_rows = (
        (
            await db.execute(
                select(TeacherAvailability).where(
                    TeacherAvailability.school_id == school_id,
                    TeacherAvailability.academic_session_id == session_id,
                    TeacherAvailability.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    tc_builders: dict[UUID, dict[str, Any]] = {}
    for uid, u in users.items():
        tc_builders[uid] = {
            "name": f"{u.first_name} {u.last_name}",
            "unavail": [],
            "avoid": [],
            "max_day": None,
            "max_week": None,
            "max_consec": None,
            "gap": 0,
        }
    for av in avail_rows:
        b = tc_builders.setdefault(
            av.teacher_id,
            {
                "name": "Teacher",
                "unavail": [],
                "avoid": [],
                "max_day": None,
                "max_week": None,
                "max_consec": None,
                "gap": 0,
            },
        )
        if av.availability_type == "UNAVAILABLE":
            b["unavail"].append((av.day_of_week, av.start_time, av.end_time))
        elif av.availability_type == "AVOID":
            b["avoid"].append((av.day_of_week, av.start_time, av.end_time))
        if av.max_periods_per_day is not None:
            b["max_day"] = av.max_periods_per_day
        if av.max_periods_per_week is not None:
            b["max_week"] = av.max_periods_per_week
        if av.max_consecutive_periods is not None:
            b["max_consec"] = av.max_consecutive_periods
        b["gap"] = max(b["gap"], av.min_gap_minutes_between_categories)

    teacher_constraints = {
        uid: TeacherConstraintSpec(
            teacher_id=uid,
            teacher_name=b["name"],
            unavailable_windows=tuple(b["unavail"]),
            avoid_windows=tuple(b["avoid"]),
            max_periods_per_day=b["max_day"],
            max_periods_per_week=b["max_week"],
            max_consecutive_periods=b["max_consec"],
            min_gap_minutes_between_categories=b["gap"],
        )
        for uid, b in tc_builders.items()
    }

    # Elective student disjointness across mixed arms
    regs = (
        await db.execute(
            select(
                StudentSubjectRegistration.student_enrollment_id,
                StudentSubjectRegistration.class_subject_id,
            ).where(
                StudentSubjectRegistration.school_id == school_id,
                StudentSubjectRegistration.is_active.is_(True),
            )
        )
    ).all()
    cs_by_student: dict[UUID, list[UUID]] = {}
    for eid, csid in regs:
        cs_by_student.setdefault(eid, []).append(csid)
    disjoint_pairs: set[tuple[UUID, UUID]] = set()
    for cs_list in cs_by_student.values():
        if len(cs_list) > 1 and len(cs_list) <= 20:
            for i in range(len(cs_list)):
                for j in range(i + 1, len(cs_list)):
                    a, b = cs_list[i], cs_list[j]
                    if a != b:
                        disjoint_pairs.add((min(a, b), max(a, b)))

    return {
        "slot_specs": slot_specs,
        "cs_specs": cs_specs,
        "rooms": rooms,
        "sync_specs": sync_specs,
        "teacher_constraints": teacher_constraints,
        "disjoint_pairs": disjoint_pairs,
        "missing_schedule_categories": sorted(missing_schedule_categories),
    }


_CATEGORY_LABELS = {
    "PRIMARY": "Primary",
    "JUNIOR_SECONDARY": "Junior Secondary",
    "SENIOR_SECONDARY": "Senior Secondary",
}


# ---------------------------------------------------------------------------
# Relaxation suggestions: map a solver diagnostic's structured reason data
# back to one concrete, reversible rule change the admin can accept.
# ---------------------------------------------------------------------------

# Reasons are ranked by how many weekly slots they blocked, but the most
# *frequent* reason isn't always the best fix: "this class arm is busy most
# of the week" is just normal fullness and fixing it means reshaping the
# whole bell schedule, whereas "the same teacher already teaches this exact
# subject to another arm" is a precise, zero-side-effect merge. Prefer the
# surgical fixes first regardless of raw count.
_UNPLACED_FIX_PRIORITY = [
    "TEACHER_BOOKED",
    "ROOM_BOOKED",
    "MAX_PER_DAY",
    "TEACHER_MAX_PER_DAY",
    "TEACHER_UNAVAILABLE",
    "CLASS_ARM_BUSY",
]

_MANUAL_REVIEW_EXPLANATIONS = {
    "TEACHER_BOOKED": (
        "This teacher is double-booked across two different subjects, not two arms of the "
        "same one -- relaxing this automatically risks a real clash. Reassign one of the two "
        "classes to another qualified teacher."
    ),
    "DISJOINT_ELECTIVE_CONFLICT": (
        "These are two electives the same students are registered for -- forcing them into "
        "the same slot would create a real clash in those students' own timetables. Combine "
        "them under Simultaneous Electives if they can share a room, or adjust registrations."
    ),
    "NO_ADJACENT_DOUBLE_SLOT": (
        "There's no free back-to-back pair of periods for this double period. Add an extra "
        "period to the bell schedule or free up an adjacent slot manually."
    ),
    "NO_MATCHING_SLOT_OTHER_SCHEDULE": (
        "The paired class arm uses a different bell schedule with no matching period -- align "
        "both schedules' period times first."
    ),
    "INVALID_DOUBLE_PERIOD_RULE": (
        "This subject's own rule asks for more double periods than its total weekly periods "
        "allow -- fix the rule directly in Subjects & Double Periods."
    ),
    "INVALID_MAX_PER_DAY": (
        "This subject requires double periods but its daily cap is below 2 -- fix the rule "
        "directly in Subjects & Double Periods."
    ),
    "MISSING_BELL_SCHEDULE": "Create a bell schedule for this category first.",
}


def _manual_review_reason(code: str | None) -> str:
    return _MANUAL_REVIEW_EXPLANATIONS.get(
        code or "",
        "This needs a manual decision -- there's no change we can make automatically "
        "without risking a different conflict.",
    )


async def _resolve_class_subject_context(
    db: AsyncSession, school_id: UUID, class_subject_id: UUID
) -> tuple[ClassSubject, SubjectOffering, Subject, ClassGroup] | None:
    cs = (
        await db.execute(
            select(ClassSubject).where(
                ClassSubject.id == class_subject_id,
                ClassSubject.school_id == school_id,
                ClassSubject.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if cs is None:
        return None
    off = (
        await db.execute(select(SubjectOffering).where(SubjectOffering.id == cs.subject_offering_id))
    ).scalar_one_or_none()
    cg = (await db.execute(select(ClassGroup).where(ClassGroup.id == cs.class_group_id))).scalar_one_or_none()
    if off is None or cg is None:
        return None
    subj = (await db.execute(select(Subject).where(Subject.id == off.subject_id))).scalar_one_or_none()
    if subj is None:
        return None
    return cs, off, subj, cg


def _effective_rule_values(rule: SubjectSchedulingRule | None, subj: Subject) -> dict[str, Any]:
    # Mirrors the same defaults `_load_solver_context` feeds the solver, so a
    # relaxation always starts from exactly what the last generation actually used.
    default_periods = 4 if subj.code in ("ENG", "MTH") else 2
    default_doubles = 1 if subj.code in ("ENG", "MTH", "PHY", "CHM", "BIO") else 0
    return {
        "periods_per_week": rule.periods_per_week if rule else default_periods,
        "double_periods_per_week": rule.double_periods_per_week if rule else default_doubles,
        "allow_double_period_split": rule.allow_double_period_split if rule else False,
        "max_periods_per_day": rule.max_periods_per_day if rule else 2,
        "prefer_morning": (
            rule.prefer_morning if rule else subj.code in ("ENG", "MTH", "PHY", "CHM", "BIO")
        ),
        "preferred_room_id": rule.preferred_room_id if rule else None,
        "room_only_for_double_periods": rule.room_only_for_double_periods if rule else True,
    }


async def _find_alternate_room(
    db: AsyncSession, school_id: UUID, current_room_id: str | None
) -> Room | None:
    if not current_room_id:
        return None
    current = (
        await db.execute(select(Room).where(Room.id == UUID(current_room_id)))
    ).scalar_one_or_none()
    if current is None:
        return None
    candidates = (
        (
            await db.execute(
                select(Room).where(
                    Room.school_id == school_id,
                    Room.room_type == current.room_type,
                    Room.id != current.id,
                    Room.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return None
    usage = dict(
        (
            await db.execute(
                select(SubjectSchedulingRule.preferred_room_id, func.count())
                .where(
                    SubjectSchedulingRule.school_id == school_id,
                    SubjectSchedulingRule.preferred_room_id.isnot(None),
                    SubjectSchedulingRule.deleted_at.is_(None),
                )
                .group_by(SubjectSchedulingRule.preferred_room_id)
            )
        ).all()
    )
    candidates.sort(key=lambda r: usage.get(r.id, 0))
    return candidates[0]


async def _build_suggestion_for_reason(
    db: AsyncSession,
    school_id: UUID,
    idx: int,
    diagnostic_code: str,
    diagnostic_message: str,
    reason_code: str,
    data: dict[str, Any],
) -> RelaxationSuggestion | None:
    if reason_code == "TEACHER_BOOKED":
        this_id = data.get("class_subject_id")
        other_id = data.get("other_class_subject_id")
        if not this_id or not other_id:
            return None
        ctx = await _resolve_class_subject_context(db, school_id, UUID(this_id))
        other_ctx = await _resolve_class_subject_context(db, school_id, UUID(other_id))
        if ctx is None or other_ctx is None:
            return None
        _, _, subj, cg = ctx
        _, _, _, other_cg = other_ctx
        return RelaxationSuggestion(
            id=f"{idx}:{reason_code}",
            diagnostic_index=idx,
            diagnostic_code=diagnostic_code,
            diagnostic_message=diagnostic_message,
            auto_fixable=True,
            title=f"Combine into a shared {subj.name} session",
            description=(
                f"{cg.display_name} and {other_cg.display_name} will share the same teacher, "
                f"room and time slot for {subj.name} -- one joint session instead of two "
                "separate ones that can't both fit the same teacher's week."
            ),
            action_type="MERGE_SYNC_GROUP",
            action_payload={"class_subject_id": this_id, "other_class_subject_id": other_id},
        )

    if reason_code in ("ROOM_BOOKED", "ROOM_CAPACITY_EXCEEDED"):
        csid = data.get("class_subject_id")
        if not csid:
            return None
        ctx = await _resolve_class_subject_context(db, school_id, UUID(csid))
        if ctx is None:
            return None
        _, _, subj, cg = ctx
        alt = await _find_alternate_room(db, school_id, data.get("room_id"))
        if alt is None:
            return None
        return RelaxationSuggestion(
            id=f"{idx}:{reason_code}",
            diagnostic_index=idx,
            diagnostic_code=diagnostic_code,
            diagnostic_message=diagnostic_message,
            auto_fixable=True,
            title=f"Move {subj.name} to {alt.name}",
            description=(
                f"{cg.display_name}'s {subj.name} will use {alt.name} instead -- the same kind "
                "of room, with less competing demand."
            ),
            action_type="CHANGE_ROOM",
            action_payload={"class_subject_id": csid, "room_id": str(alt.id)},
        )

    if reason_code in ("CLASS_ARM_BUSY", "CLASS_CAPACITY_EXCEEDED"):
        bell_schedule_id = data.get("bell_schedule_id")
        if not bell_schedule_id:
            return None
        schedule = await _get_bell_schedule_or_404(db, school_id, UUID(bell_schedule_id))
        cg_name = ""
        class_group_id = data.get("class_group_id")
        if class_group_id:
            cg = (
                await db.execute(select(ClassGroup).where(ClassGroup.id == UUID(class_group_id)))
            ).scalar_one_or_none()
            cg_name = cg.display_name if cg else ""
        return RelaxationSuggestion(
            id=f"{idx}:{reason_code}",
            diagnostic_index=idx,
            diagnostic_code=diagnostic_code,
            diagnostic_message=diagnostic_message,
            auto_fixable=True,
            title="Add one more teaching period to the week",
            description=(
                f"{schedule.name}{f' ({cg_name})' if cg_name else ''} doesn't have a free period "
                "left this week. This adds one more teaching period (un-truncating Friday if "
                "it's the short day, otherwise appending to the lightest day)."
            ),
            action_type="ADD_BELL_CAPACITY",
            action_payload={"bell_schedule_id": bell_schedule_id},
        )

    if reason_code == "MAX_PER_DAY":
        csid = data.get("class_subject_id")
        if not csid:
            return None
        ctx = await _resolve_class_subject_context(db, school_id, UUID(csid))
        if ctx is None:
            return None
        _, _, subj, cg = ctx
        return RelaxationSuggestion(
            id=f"{idx}:{reason_code}",
            diagnostic_index=idx,
            diagnostic_code=diagnostic_code,
            diagnostic_message=diagnostic_message,
            auto_fixable=True,
            title=f"Allow one more {subj.name} period per day",
            description=f"Raises {subj.name}'s daily limit for {cg.display_name} by 1 period.",
            action_type="RAISE_SUBJECT_DAILY_CAP",
            action_payload={"class_subject_id": csid},
        )

    if reason_code in ("TEACHER_MAX_PER_DAY", "TEACHER_WEEKLY_LOAD_EXCEEDED"):
        tid = data.get("teacher_id")
        session_id = data.get("academic_session_id")
        if not tid or not session_id:
            return None
        teacher = (await db.execute(select(User).where(User.id == UUID(tid)))).scalar_one_or_none()
        tname = f"{teacher.first_name} {teacher.last_name}" if teacher else "This teacher"
        cap_type = "day" if reason_code == "TEACHER_MAX_PER_DAY" else "week"
        return RelaxationSuggestion(
            id=f"{idx}:{reason_code}",
            diagnostic_index=idx,
            diagnostic_code=diagnostic_code,
            diagnostic_message=diagnostic_message,
            auto_fixable=True,
            title=f"Raise {tname}'s {'daily' if cap_type == 'day' else 'weekly'} period limit",
            description=(
                f"Increases {tname}'s {'daily' if cap_type == 'day' else 'weekly'} teaching cap "
                "by the minimum needed to fit this class."
            ),
            action_type="RAISE_TEACHER_CAP",
            action_payload={
                "teacher_id": tid,
                "academic_session_id": session_id,
                "cap_type": cap_type,
                "needed_weekly_cap": data.get("needed_weekly_cap"),
            },
        )

    if reason_code == "TEACHER_UNAVAILABLE":
        tid = data.get("teacher_id")
        session_id = data.get("academic_session_id")
        if not tid or not session_id:
            return None
        teacher = (await db.execute(select(User).where(User.id == UUID(tid)))).scalar_one_or_none()
        tname = f"{teacher.first_name} {teacher.last_name}" if teacher else "This teacher"
        return RelaxationSuggestion(
            id=f"{idx}:{reason_code}",
            diagnostic_index=idx,
            diagnostic_code=diagnostic_code,
            diagnostic_message=diagnostic_message,
            auto_fixable=True,
            title=f"Soften {tname}'s unavailability to a preference",
            description=(
                f"{tname} will only be *avoided* at that time instead of being hard-blocked -- "
                "they may occasionally still be scheduled then if nothing else fits."
            ),
            action_type="SOFTEN_TEACHER_AVAILABILITY",
            action_payload={
                "teacher_id": tid,
                "academic_session_id": session_id,
                "day_of_week": data.get("day_of_week"),
            },
        )

    return None


async def build_relaxation_suggestions(
    db: AsyncSession, school_id: UUID, tt: Timetable
) -> list[RelaxationSuggestion]:
    term = (
        await db.execute(select(AcademicTerm).where(AcademicTerm.id == tt.academic_term_id))
    ).scalar_one()
    session_id = str(term.academic_session_id)

    summary = tt.generation_summary or {}
    diagnostics = summary.get("diagnostics") or []
    suggestions: list[RelaxationSuggestion] = []

    for idx, diag in enumerate(diagnostics):
        code = diag.get("code") or "UNKNOWN"
        message = diag.get("message", "")
        details = diag.get("details") or {}

        if code == "UNPLACED_PERIOD":
            reasons = details.get("reasons") or []
            reasons_by_code = {r.get("code"): r for r in reasons}
            picked_code: str | None = None
            picked_data: dict[str, Any] = {}
            for candidate_code in _UNPLACED_FIX_PRIORITY:
                reason = reasons_by_code.get(candidate_code)
                if reason is None:
                    continue
                rdata = dict(reason.get("data") or {})
                if candidate_code == "TEACHER_BOOKED" and not rdata.get("same_subject"):
                    continue
                rdata.setdefault("academic_session_id", session_id)
                picked_code, picked_data = candidate_code, rdata
                break
            suggestion = (
                await _build_suggestion_for_reason(db, school_id, idx, code, message, picked_code, picked_data)
                if picked_code
                else None
            )
            if suggestion is None:
                fallback_code = reasons[0].get("code") if reasons else None
                suggestion = RelaxationSuggestion(
                    id=f"{idx}:none",
                    diagnostic_index=idx,
                    diagnostic_code=code,
                    diagnostic_message=message,
                    auto_fixable=False,
                    title="Needs your judgement",
                    description=_manual_review_reason(fallback_code),
                )
            suggestions.append(suggestion)
            continue

        if code in ("CLASS_CAPACITY_EXCEEDED", "ROOM_CAPACITY_EXCEEDED", "TEACHER_WEEKLY_LOAD_EXCEEDED"):
            data = dict(details)
            data.setdefault("academic_session_id", session_id)
            suggestion = await _build_suggestion_for_reason(db, school_id, idx, code, message, code, data)
            if suggestion is None:
                suggestion = RelaxationSuggestion(
                    id=f"{idx}:none",
                    diagnostic_index=idx,
                    diagnostic_code=code,
                    diagnostic_message=message,
                    auto_fixable=False,
                    title="Needs your judgement",
                    description=_manual_review_reason(code),
                )
            suggestions.append(suggestion)
            continue

        suggestions.append(
            RelaxationSuggestion(
                id=f"{idx}:none",
                diagnostic_index=idx,
                diagnostic_code=code,
                diagnostic_message=message,
                auto_fixable=False,
                title="Needs your judgement",
                description=_manual_review_reason(code),
            )
        )

    return suggestions


async def _add_bell_capacity(
    db: AsyncSession, school_id: UUID, schedule_id: UUID, audit: AuditContext
) -> None:
    schedule = await _get_bell_schedule_or_404(db, school_id, schedule_id)
    active_slots = [s for s in schedule.slots if s.deleted_at is None]
    by_day: dict[int, list[TimetableSlot]] = {}
    for s in active_slots:
        by_day.setdefault(s.day_of_week, []).append(s)
    for day_slots in by_day.values():
        day_slots.sort(key=lambda s: s.period_order)

    weekdays = [d for d in (1, 2, 3, 4, 5) if d in by_day]
    if not weekdays:
        return
    teaching_counts = {d: sum(1 for s in by_day[d] if s.slot_type == "TEACHING") for d in weekdays}
    other_counts = [teaching_counts[d] for d in weekdays if d != 5]
    if 5 in teaching_counts and other_counts and teaching_counts[5] < min(other_counts):
        target_day = 5
    else:
        target_day = min(weekdays, key=lambda d: teaching_counts.get(d, 0))

    day_slots = by_day[target_day]
    last = day_slots[-1]
    teaching_on_day = [s for s in day_slots if s.slot_type == "TEACHING"]
    period_len_minutes = 40
    if teaching_on_day:
        t0 = teaching_on_day[0]
        computed = (t0.end_time.hour * 60 + t0.end_time.minute) - (
            t0.start_time.hour * 60 + t0.start_time.minute
        )
        if computed > 0:
            period_len_minutes = computed

    new_start = last.end_time
    new_end_total = new_start.hour * 60 + new_start.minute + period_len_minutes
    new_end = time(hour=(new_end_total // 60) % 24, minute=new_end_total % 60)

    existing_slot_ins = [
        TimetableSlotCreate(
            day_of_week=s.day_of_week,
            period_order=s.period_order,
            label=s.label,
            start_time=s.start_time,
            end_time=s.end_time,
            slot_type=SlotType(s.slot_type),
        )
        for s in active_slots
    ]
    new_slot_in = TimetableSlotCreate(
        day_of_week=target_day,
        period_order=last.period_order + 1,
        label=f"Period {len(teaching_on_day) + 1}",
        start_time=new_start,
        end_time=new_end,
        slot_type=SlotType.TEACHING,
    )
    await replace_bell_schedule_slots(
        db,
        school_id,
        schedule_id,
        BellScheduleSlotsReplaceRequest(slots=[*existing_slot_ins, new_slot_in]),
        audit,
    )


async def apply_relaxation(
    db: AsyncSession, school_id: UUID, req: ApplyRelaxationRequest, audit: AuditContext
) -> dict[str, Any]:
    payload = req.action_payload

    if req.action_type == "MERGE_SYNC_GROUP":
        this_id = UUID(payload["class_subject_id"])
        other_id = UUID(payload["other_class_subject_id"])
        existing_item = (
            await db.execute(
                select(TimetableSyncGroupItem).where(
                    TimetableSyncGroupItem.school_id == school_id,
                    TimetableSyncGroupItem.class_subject_id == other_id,
                    TimetableSyncGroupItem.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing_item is not None:
            sg = (
                await db.execute(
                    select(TimetableSyncGroup)
                    .options(selectinload(TimetableSyncGroup.items))
                    .where(TimetableSyncGroup.id == existing_item.sync_group_id)
                )
            ).scalar_one()
            existing_ids = [i.class_subject_id for i in sg.items if i.deleted_at is None]
            await update_sync_group(
                db,
                school_id,
                sg.id,
                SyncGroupUpdateRequest(
                    class_subject_ids=list(dict.fromkeys([*existing_ids, this_id]))
                ),
                audit,
            )
        else:
            ctx = await _resolve_class_subject_context(db, school_id, this_id)
            if ctx is None:
                raise ResourceNotFoundException("class subject", this_id)
            _, _, subj, cg = ctx
            await create_sync_group(
                db,
                school_id,
                SyncGroupCreateRequest(
                    academic_session_id=cg.academic_session_id,
                    name=f"{subj.name} (auto-merged)",
                    sync_type=SyncType.JOINT_CLASS,
                    class_subject_ids=[this_id, other_id],
                ),
                audit,
            )
        return {"applied": True}

    if req.action_type == "CHANGE_ROOM":
        csid = UUID(payload["class_subject_id"])
        room_id = UUID(payload["room_id"])
        ctx = await _resolve_class_subject_context(db, school_id, csid)
        if ctx is None:
            raise ResourceNotFoundException("class subject", csid)
        _, _, subj, cg = ctx
        rules = list(
            (
                await db.execute(
                    select(SubjectSchedulingRule).where(
                        SubjectSchedulingRule.school_id == school_id,
                        SubjectSchedulingRule.academic_session_id == cg.academic_session_id,
                        SubjectSchedulingRule.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        rule = _resolve_rule_for_class_subject(
            rules, level_id=cg.academic_level_id, department_id=cg.department_id, subject_id=subj.id
        )
        values = _effective_rule_values(rule, subj)
        values["preferred_room_id"] = room_id
        await upsert_scheduling_rule(
            db,
            school_id,
            SubjectSchedulingRuleUpsertRequest(
                academic_session_id=cg.academic_session_id,
                academic_level_id=rule.academic_level_id if rule else cg.academic_level_id,
                department_id=rule.department_id if rule else cg.department_id,
                subject_id=subj.id,
                **values,
            ),
            audit,
        )
        return {"applied": True}

    if req.action_type == "ADD_BELL_CAPACITY":
        await _add_bell_capacity(db, school_id, UUID(payload["bell_schedule_id"]), audit)
        return {"applied": True}

    if req.action_type == "RAISE_SUBJECT_DAILY_CAP":
        csid = UUID(payload["class_subject_id"])
        ctx = await _resolve_class_subject_context(db, school_id, csid)
        if ctx is None:
            raise ResourceNotFoundException("class subject", csid)
        _, _, subj, cg = ctx
        rules = list(
            (
                await db.execute(
                    select(SubjectSchedulingRule).where(
                        SubjectSchedulingRule.school_id == school_id,
                        SubjectSchedulingRule.academic_session_id == cg.academic_session_id,
                        SubjectSchedulingRule.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        rule = _resolve_rule_for_class_subject(
            rules, level_id=cg.academic_level_id, department_id=cg.department_id, subject_id=subj.id
        )
        values = _effective_rule_values(rule, subj)
        values["max_periods_per_day"] = min(6, values["max_periods_per_day"] + 1)
        await upsert_scheduling_rule(
            db,
            school_id,
            SubjectSchedulingRuleUpsertRequest(
                academic_session_id=cg.academic_session_id,
                academic_level_id=rule.academic_level_id if rule else cg.academic_level_id,
                department_id=rule.department_id if rule else cg.department_id,
                subject_id=subj.id,
                **values,
            ),
            audit,
        )
        return {"applied": True}

    if req.action_type == "RAISE_TEACHER_CAP":
        teacher_id = UUID(payload["teacher_id"])
        session_id = UUID(payload["academic_session_id"])
        cap_type = payload.get("cap_type", "day")
        needed_weekly_cap = payload.get("needed_weekly_cap")
        rows = (
            (
                await db.execute(
                    select(TeacherAvailability).where(
                        TeacherAvailability.school_id == school_id,
                        TeacherAvailability.academic_session_id == session_id,
                        TeacherAvailability.teacher_id == teacher_id,
                        TeacherAvailability.availability_type == "LOAD_CAP",
                        TeacherAvailability.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        existing = next(
            (
                r
                for r in rows
                if (r.max_periods_per_day if cap_type == "day" else r.max_periods_per_week) is not None
            ),
            None,
        )
        new_day = existing.max_periods_per_day if existing else None
        new_week = existing.max_periods_per_week if existing else None
        if cap_type == "day":
            new_day = (new_day or 0) + 1
        else:
            new_week = needed_weekly_cap if needed_weekly_cap is not None else (new_week or 0) + 1
        now = _now_utc()
        if existing is not None:
            existing.deleted_at = now
            await db.flush()
        db.add(
            TeacherAvailability(
                school_id=school_id,
                academic_session_id=session_id,
                teacher_id=teacher_id,
                day_of_week=existing.day_of_week if existing else None,
                start_time=existing.start_time if existing else None,
                end_time=existing.end_time if existing else None,
                availability_type=AvailabilityType.LOAD_CAP.value,
                max_periods_per_day=new_day,
                max_periods_per_week=new_week,
                max_consecutive_periods=existing.max_consecutive_periods if existing else None,
                min_gap_minutes_between_categories=(
                    existing.min_gap_minutes_between_categories if existing else 0
                ),
            )
        )
        record_audit_log(
            db,
            audit,
            action="TEACHER_AVAILABILITY_RELAXED",
            resource_type="TeacherAvailability",
            resource_id=teacher_id,
            diff_payload={"cap_type": cap_type},
        )
        await db.commit()
        return {"applied": True}

    if req.action_type == "SOFTEN_TEACHER_AVAILABILITY":
        teacher_id = UUID(payload["teacher_id"])
        session_id = UUID(payload["academic_session_id"])
        day_of_week = payload.get("day_of_week")
        rows = (
            (
                await db.execute(
                    select(TeacherAvailability).where(
                        TeacherAvailability.school_id == school_id,
                        TeacherAvailability.academic_session_id == session_id,
                        TeacherAvailability.teacher_id == teacher_id,
                        TeacherAvailability.availability_type == "UNAVAILABLE",
                        TeacherAvailability.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        match = next((r for r in rows if r.day_of_week is None or r.day_of_week == day_of_week), None)
        if match is None:
            raise ResourceNotFoundException("teacher availability window", teacher_id)
        now = _now_utc()
        match.deleted_at = now
        await db.flush()
        db.add(
            TeacherAvailability(
                school_id=school_id,
                academic_session_id=session_id,
                teacher_id=teacher_id,
                day_of_week=match.day_of_week,
                start_time=match.start_time,
                end_time=match.end_time,
                availability_type=AvailabilityType.AVOID.value,
                max_periods_per_day=None,
                max_periods_per_week=None,
                max_consecutive_periods=None,
                min_gap_minutes_between_categories=match.min_gap_minutes_between_categories,
            )
        )
        record_audit_log(
            db,
            audit,
            action="TEACHER_AVAILABILITY_SOFTENED",
            resource_type="TeacherAvailability",
            resource_id=teacher_id,
            diff_payload={},
        )
        await db.commit()
        return {"applied": True}

    raise ValidationException(f"Unsupported relaxation action: {req.action_type}")


async def run_timetable_generation(
    db: AsyncSession, school_id: UUID, timetable_id: UUID
) -> dict[str, Any]:
    tt = await _get_timetable_or_404(db, school_id, timetable_id)
    tt.generation_status = "RUNNING"
    await db.flush()

    ctx = await _load_solver_context(db, school_id, tt)
    if ctx["missing_schedule_categories"]:
        cats = ", ".join(
            _CATEGORY_LABELS.get(c, c.replace("_", " ").title())
            for c in ctx["missing_schedule_categories"]
        )
        tt.generation_status = "FAILED"
        tt.generation_summary = {
            "success": False,
            "diagnostics": [
                {
                    "code": "MISSING_BELL_SCHEDULE",
                    "message": f"Please create a bell schedule first for: {cats}.",
                }
            ],
        }
        await db.commit()
        return tt.generation_summary

    existing_pinned = (
        (
            await db.execute(
                select(TimetableEntry).where(
                    TimetableEntry.school_id == school_id,
                    TimetableEntry.timetable_id == tt.id,
                    TimetableEntry.is_pinned.is_(True),
                    TimetableEntry.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    pinned_specs = [
        PinnedEntrySpec(
            class_group_id=e.class_group_id,
            class_subject_id=e.class_subject_id,
            timetable_slot_id=e.timetable_slot_id,
            room_id=e.room_id,
            double_period_pair_id=e.double_period_pair_id,
        )
        for e in existing_pinned
    ]

    result: TimetableSolveResult = solve_timetable(
        slots=ctx["slot_specs"],
        class_subjects=ctx["cs_specs"],
        rooms=ctx["rooms"],
        sync_groups=ctx["sync_specs"],
        teacher_constraints=ctx["teacher_constraints"],
        pinned_entries=pinned_specs,
        disjoint_class_subject_pairs=ctx["disjoint_pairs"],
    )

    # Soft-delete unpinned previous entries for this draft
    now = _now_utc()
    await db.execute(
        update(TimetableEntry)
        .where(
            TimetableEntry.school_id == school_id,
            TimetableEntry.timetable_id == tt.id,
            TimetableEntry.is_pinned.is_(False),
            TimetableEntry.deleted_at.is_(None),
        )
        .values(deleted_at=now)
    )
    await db.flush()

    for solved in result.entries:
        if solved.is_pinned:
            continue
        entry = TimetableEntry(
            school_id=school_id,
            timetable_id=tt.id,
            class_group_id=solved.class_group_id,
            class_subject_id=solved.class_subject_id,
            teacher_id=solved.teacher_ids[0] if solved.teacher_ids else None,
            timetable_slot_id=solved.timetable_slot_id,
            room_id=solved.room_id,
            sync_group_id=solved.sync_group_id,
            is_double_period_part=solved.is_double_period_part,
            double_period_pair_id=solved.double_period_pair_id,
            is_pinned=False,
        )
        db.add(entry)
        await db.flush()
        for tid in solved.teacher_ids:
            db.add(
                TimetableEntryTeacher(
                    school_id=school_id, timetable_entry_id=entry.id, teacher_id=tid
                )
            )

    tt.generation_status = (
        "COMPLETED" if result.success else ("PARTIAL" if result.entries else "FAILED")
    )
    tt.generation_summary = {
        "success": result.success,
        "entries_placed": len(result.entries),
        "unplaced_count": result.unplaced_count,
        "soft_score": result.soft_score,
        "diagnostics": [
            {"code": d.code, "message": d.message, "details": d.details} for d in result.diagnostics
        ],
    }
    await db.commit()
    return tt.generation_summary


async def validate_entry_move(
    db: AsyncSession,
    school_id: UUID,
    timetable_id: UUID,
    entry_id: UUID,
    req: TimetableEntryMoveValidateRequest,
) -> TimetableEntryMoveValidateResponse:
    tt = await _get_timetable_or_404(db, school_id, timetable_id)
    entry = (
        await db.execute(
            select(TimetableEntry).where(
                TimetableEntry.id == entry_id,
                TimetableEntry.timetable_id == tt.id,
                TimetableEntry.school_id == school_id,
                TimetableEntry.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise ResourceNotFoundException("timetable entry", entry_id)

    ctx = await _load_solver_context(db, school_id, tt)
    slots_by_id = {s.id: s for s in ctx["slot_specs"]}
    cs_by_id = {cs.class_subject_id: cs for cs in ctx["cs_specs"]}
    target_slot = slots_by_id.get(req.target_slot_id)
    moving_cs = cs_by_id.get(entry.class_subject_id)
    if target_slot is None or moving_cs is None:
        raise ResourceNotFoundException("timetable slot", req.target_slot_id)

    target_second_slot = None
    if entry.double_period_pair_id is not None:
        target_second_slot = next(
            (
                s
                for s in ctx["slot_specs"]
                if s.bell_schedule_id == target_slot.bell_schedule_id
                and s.day_of_week == target_slot.day_of_week
                and s.period_order == target_slot.period_order + 1
            ),
            None,
        )

    other_entries = (
        (
            await db.execute(
                select(TimetableEntry)
                .options(selectinload(TimetableEntry.entry_teachers))
                .where(
                    TimetableEntry.timetable_id == tt.id,
                    TimetableEntry.school_id == school_id,
                    TimetableEntry.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    solved_existing = [
        SolvedEntry(
            class_group_id=e.class_group_id,
            class_subject_id=e.class_subject_id,
            teacher_ids=tuple(et.teacher_id for et in e.entry_teachers if et.deleted_at is None)
            or ((e.teacher_id,) if e.teacher_id else ()),
            timetable_slot_id=e.timetable_slot_id,
            room_id=e.room_id,
            sync_group_id=e.sync_group_id,
            is_double_period_part=e.is_double_period_part,
            double_period_pair_id=e.double_period_pair_id,
            is_pinned=e.is_pinned,
        )
        for e in other_entries
        if e.id != entry.id
        and (
            entry.double_period_pair_id is None
            or e.double_period_pair_id != entry.double_period_pair_id
        )
    ]

    diags = validate_manual_move(
        target_slot=target_slot,
        moving_cs=moving_cs,
        existing_entries=solved_existing,
        slots_by_id=slots_by_id,
        cs_by_id=cs_by_id,
        teacher_constraints=ctx["teacher_constraints"],
        sync_groups=ctx["sync_specs"],
        target_second_slot=target_second_slot,
        moving_room_id=req.target_room_id if req.target_room_id is not None else entry.room_id,
    )
    return TimetableEntryMoveValidateResponse(
        valid=len(diags) == 0,
        conflicts=[{"code": d.code, "message": d.message} for d in diags],
    )


async def update_timetable_entry(
    db: AsyncSession,
    school_id: UUID,
    timetable_id: UUID,
    entry_id: UUID,
    req: TimetableEntryUpdateRequest,
    audit: AuditContext,
) -> list[TimetableEntry]:
    tt = await _get_timetable_or_404(db, school_id, timetable_id)
    if tt.status == "PUBLISHED":
        raise ConflictException(
            message="This timetable is already published. Clone it into a new draft to make changes.",
            code="TIMETABLE_PUBLISHED_LOCKED",
        )
    entry = (
        await db.execute(
            select(TimetableEntry).where(
                TimetableEntry.id == entry_id,
                TimetableEntry.timetable_id == tt.id,
                TimetableEntry.school_id == school_id,
                TimetableEntry.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise ResourceNotFoundException("timetable entry", entry_id)

    paired_entries = [entry]
    if entry.double_period_pair_id is not None:
        paired_entries = list(
            (
                await db.execute(
                    select(TimetableEntry).where(
                        TimetableEntry.timetable_id == tt.id,
                        TimetableEntry.double_period_pair_id == entry.double_period_pair_id,
                        TimetableEntry.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    if req.is_pinned is not None:
        for pe in paired_entries:
            pe.is_pinned = req.is_pinned
    if req.room_id is not None:
        for pe in paired_entries:
            pe.room_id = req.room_id

    if req.timetable_slot_id is not None and req.timetable_slot_id != entry.timetable_slot_id:
        validation = await validate_entry_move(
            db,
            school_id,
            timetable_id,
            entry_id,
            TimetableEntryMoveValidateRequest(
                target_slot_id=req.timetable_slot_id, target_room_id=req.room_id
            ),
        )
        if not validation.valid:
            raise ConflictException(
                message=validation.conflicts[0]["message"],
                code=validation.conflicts[0]["code"],
            )
        target_slot = (
            await db.execute(select(TimetableSlot).where(TimetableSlot.id == req.timetable_slot_id))
        ).scalar_one()
        if len(paired_entries) == 2:
            second_slot = (
                await db.execute(
                    select(TimetableSlot).where(
                        TimetableSlot.bell_schedule_id == target_slot.bell_schedule_id,
                        TimetableSlot.day_of_week == target_slot.day_of_week,
                        TimetableSlot.period_order == target_slot.period_order + 1,
                        TimetableSlot.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if second_slot is None:
                raise ConflictException(
                    message="Double periods require two consecutive teaching slots.",
                    code="DOUBLE_PERIOD_NO_SECOND_SLOT",
                )
            paired_entries[0].timetable_slot_id = target_slot.id
            paired_entries[1].timetable_slot_id = second_slot.id
        else:
            entry.timetable_slot_id = target_slot.id

    record_audit_log(
        db,
        audit,
        action="TIMETABLE_ENTRY_UPDATED",
        resource_type="TimetableEntry",
        resource_id=entry.id,
        diff_payload={"is_pinned": entry.is_pinned, "slot_id": str(entry.timetable_slot_id)},
    )
    await db.commit()
    return paired_entries


async def delete_timetable_entry(
    db: AsyncSession, school_id: UUID, timetable_id: UUID, entry_id: UUID, audit: AuditContext
) -> None:
    tt = await _get_timetable_or_404(db, school_id, timetable_id)
    if tt.status == "PUBLISHED":
        raise ConflictException(
            message="Published timetables cannot be modified.",
            code="TIMETABLE_PUBLISHED_LOCKED",
        )
    entry = (
        await db.execute(
            select(TimetableEntry).where(
                TimetableEntry.id == entry_id,
                TimetableEntry.timetable_id == tt.id,
                TimetableEntry.school_id == school_id,
                TimetableEntry.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise ResourceNotFoundException("timetable entry", entry_id)
    now = _now_utc()
    if entry.double_period_pair_id is not None:
        await db.execute(
            update(TimetableEntry)
            .where(
                TimetableEntry.timetable_id == tt.id,
                TimetableEntry.double_period_pair_id == entry.double_period_pair_id,
            )
            .values(deleted_at=now)
        )
    else:
        entry.deleted_at = now
    record_audit_log(
        db,
        audit,
        action="TIMETABLE_ENTRY_DELETED",
        resource_type="TimetableEntry",
        resource_id=entry.id,
        diff_payload={},
    )
    await db.commit()


async def publish_timetable(
    db: AsyncSession, school_id: UUID, timetable_id: UUID, actor: User, audit: AuditContext
) -> TimetableResponse:
    tt = await _get_timetable_or_404(db, school_id, timetable_id)
    if tt.status == "PUBLISHED":
        return TimetableResponse.model_validate(tt)

    unassigned_count = len(
        (
            await db.execute(
                select(TimetableEntry.id).where(
                    TimetableEntry.timetable_id == tt.id,
                    TimetableEntry.teacher_id.is_(None),
                    TimetableEntry.deleted_at.is_(None),
                )
            )
        ).all()
    )
    if unassigned_count > 0:
        raise ValidationException(
            f"Cannot publish yet: {unassigned_count} timetable slot(s) still have no assigned teacher."
        )

    await db.execute(
        update(Timetable)
        .where(
            Timetable.school_id == school_id,
            Timetable.academic_term_id == tt.academic_term_id,
            Timetable.status == "PUBLISHED",
            Timetable.deleted_at.is_(None),
        )
        .values(status="ARCHIVED")
    )
    tt.status = "PUBLISHED"
    tt.published_at = _now_utc()
    tt.published_by_id = actor.id

    record_audit_log(
        db,
        audit,
        action="TIMETABLE_PUBLISHED",
        resource_type="Timetable",
        resource_id=tt.id,
        diff_payload={"version": tt.version},
    )
    await db.commit()
    return TimetableResponse.model_validate(tt)


async def clone_timetable(
    db: AsyncSession, school_id: UUID, timetable_id: UUID, audit: AuditContext
) -> TimetableResponse:
    src = await _get_timetable_or_404(db, school_id, timetable_id)
    clone = Timetable(
        school_id=school_id,
        academic_term_id=src.academic_term_id,
        parent_timetable_id=src.id,
        version=src.version + 1,
        name=f"{src.name} (v{src.version + 1})",
        status="DRAFT",
        generation_status=src.generation_status,
        generation_summary=src.generation_summary,
    )
    db.add(clone)
    await db.flush()

    src_entries = (
        (
            await db.execute(
                select(TimetableEntry)
                .options(selectinload(TimetableEntry.entry_teachers))
                .where(
                    TimetableEntry.timetable_id == src.id,
                    TimetableEntry.school_id == school_id,
                    TimetableEntry.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    pair_remap: dict[UUID, UUID] = {}
    for se in src_entries:
        new_pair = None
        if se.double_period_pair_id is not None:
            new_pair = pair_remap.setdefault(se.double_period_pair_id, uuid4())
        ne = TimetableEntry(
            school_id=school_id,
            timetable_id=clone.id,
            class_group_id=se.class_group_id,
            class_subject_id=se.class_subject_id,
            teacher_id=se.teacher_id,
            timetable_slot_id=se.timetable_slot_id,
            room_id=se.room_id,
            sync_group_id=se.sync_group_id,
            is_double_period_part=se.is_double_period_part,
            double_period_pair_id=new_pair,
            is_pinned=se.is_pinned,
        )
        db.add(ne)
        await db.flush()
        for et in se.entry_teachers:
            if et.deleted_at is None:
                db.add(
                    TimetableEntryTeacher(
                        school_id=school_id, timetable_entry_id=ne.id, teacher_id=et.teacher_id
                    )
                )

    record_audit_log(
        db,
        audit,
        action="TIMETABLE_CLONED",
        resource_type="Timetable",
        resource_id=clone.id,
        diff_payload={"source_id": str(src.id), "version": clone.version},
    )
    await db.commit()
    return TimetableResponse.model_validate(clone)


async def _build_entry_views(
    db: AsyncSession,
    school_id: UUID,
    tt: Timetable,
    *,
    class_group_id: UUID | None = None,
    teacher_id: UUID | None = None,
) -> tuple[list[TimetableSlotResponse], list[TimetableEntryView], list[dict[str, Any]]]:
    term = (
        await db.execute(select(AcademicTerm).where(AcademicTerm.id == tt.academic_term_id))
    ).scalar_one()

    stmt = (
        select(TimetableEntry)
        .options(selectinload(TimetableEntry.entry_teachers))
        .where(
            TimetableEntry.timetable_id == tt.id,
            TimetableEntry.school_id == school_id,
            TimetableEntry.deleted_at.is_(None),
        )
    )
    if class_group_id is not None:
        stmt = stmt.where(TimetableEntry.class_group_id == class_group_id)
    if teacher_id is not None:
        stmt = stmt.where(TimetableEntry.teacher_id == teacher_id)

    entries = (await db.execute(stmt)).scalars().all()
    cgs = {
        cg.id: cg
        for cg in (
            await db.execute(
                select(ClassGroup).where(
                    ClassGroup.school_id == school_id,
                    ClassGroup.academic_session_id == term.academic_session_id,
                    ClassGroup.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    levels = {
        lvl.id: lvl
        for lvl in (
            await db.execute(
                select(AcademicLevel).where(
                    AcademicLevel.school_id == school_id, AcademicLevel.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    }
    cs_map = {
        cs.id: cs
        for cs in (
            await db.execute(
                select(ClassSubject).where(
                    ClassSubject.school_id == school_id, ClassSubject.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    }
    off_map = {
        o.id: o
        for o in (
            await db.execute(
                select(SubjectOffering).where(
                    SubjectOffering.school_id == school_id, SubjectOffering.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    }
    subj_map = {
        s.id: s
        for s in (
            await db.execute(
                select(Subject).where(Subject.school_id == school_id, Subject.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    }
    users = {
        u.id: u
        for u in (
            await db.execute(
                select(User).where(User.school_id == school_id, User.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    }
    rooms = {
        r.id: r
        for r in (
            await db.execute(
                select(Room).where(Room.school_id == school_id, Room.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    }
    # A single class arm follows exactly one bell schedule: the academic-level
    # override if one exists, else the category-wide default (same precedence
    # used when solving). Without this, two different schedules' independently
    # numbered periods (e.g. a JSS default plus an SS2 override) get merged
    # into one grid, producing duplicate/overlapping "Period 1", "Period 2"...
    # columns. Teacher and master views legitimately span multiple schedules,
    # so this filter only applies when a single class_group_id is requested.
    resolved_bell_schedule_id: UUID | None = None
    bell_schedule_filter_active = False
    if class_group_id is not None:
        bell_schedule_filter_active = True
        cg = cgs.get(class_group_id)
        lvl = levels.get(cg.academic_level_id) if cg else None
        if lvl is not None:
            bell_schedules = (
                (
                    await db.execute(
                        select(BellSchedule).where(
                            BellSchedule.school_id == school_id,
                            BellSchedule.academic_session_id == term.academic_session_id,
                            BellSchedule.is_active.is_(True),
                            BellSchedule.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            schedule_by_level = {
                s.academic_level_id: s for s in bell_schedules if s.academic_level_id is not None
            }
            schedule_by_cat = {
                s.level_category: s for s in bell_schedules if s.academic_level_id is None
            }
            resolved = schedule_by_level.get(lvl.id) or schedule_by_cat.get(lvl.category)
            if resolved is not None:
                resolved_bell_schedule_id = resolved.id

    if bell_schedule_filter_active and resolved_bell_schedule_id is None:
        slots: list[TimetableSlot] = []
    else:
        slot_stmt = (
            select(TimetableSlot)
            .join(BellSchedule, BellSchedule.id == TimetableSlot.bell_schedule_id)
            .where(
                TimetableSlot.school_id == school_id,
                TimetableSlot.deleted_at.is_(None),
                BellSchedule.academic_session_id == term.academic_session_id,
                BellSchedule.deleted_at.is_(None),
            )
        )
        if resolved_bell_schedule_id is not None:
            slot_stmt = slot_stmt.where(TimetableSlot.bell_schedule_id == resolved_bell_schedule_id)
        slots = (
            (
                await db.execute(
                    slot_stmt.order_by(TimetableSlot.day_of_week, TimetableSlot.period_order)
                )
            )
            .scalars()
            .all()
        )
    slots_by_id = {s.id: s for s in slots}

    views: list[TimetableEntryView] = []
    for e in entries:
        cg = cgs.get(e.class_group_id)
        cs = cs_map.get(e.class_subject_id)
        off = off_map.get(cs.subject_offering_id) if cs else None
        subj = subj_map.get(off.subject_id) if off else None
        lvl = levels.get(cg.academic_level_id) if cg else None
        slot = slots_by_id.get(e.timetable_slot_id)
        if cg is None or subj is None or slot is None or lvl is None:
            continue
        tids = [et.teacher_id for et in e.entry_teachers if et.deleted_at is None]
        if not tids and e.teacher_id:
            tids = [e.teacher_id]
        t_names = [
            f"{users[tid].first_name} {users[tid].last_name}" for tid in tids if tid in users
        ]
        room = rooms.get(e.room_id) if e.room_id else None
        views.append(
            TimetableEntryView(
                id=e.id,
                class_group_id=cg.id,
                class_group_name=cg.display_name,
                level_category=lvl.category,
                class_subject_id=e.class_subject_id,
                subject_id=subj.id,
                subject_code=subj.code,
                subject_name=subj.name,
                teacher_id=e.teacher_id,
                teacher_name=t_names[0] if t_names else None,
                teacher_names=t_names,
                timetable_slot_id=slot.id,
                day_of_week=slot.day_of_week,
                period_order=slot.period_order,
                slot_label=slot.label,
                start_time=slot.start_time,
                end_time=slot.end_time,
                room_id=e.room_id,
                room_name=room.name if room else None,
                sync_group_id=e.sync_group_id,
                is_double_period_part=e.is_double_period_part,
                double_period_pair_id=e.double_period_pair_id,
                is_pinned=e.is_pinned,
            )
        )

    cg_summaries = [
        {
            "id": str(cg.id),
            "display_name": cg.display_name,
            "level_category": levels[cg.academic_level_id].category
            if cg.academic_level_id in levels
            else "SECONDARY",
        }
        for cg in sorted(cgs.values(), key=lambda x: x.display_name)
    ]
    return [TimetableSlotResponse.model_validate(s) for s in slots], views, cg_summaries


async def get_class_group_timetable(
    db: AsyncSession, school_id: UUID, timetable_id: UUID, class_group_id: UUID
) -> TimetableGridResponse:
    tt = await _get_timetable_or_404(db, school_id, timetable_id)
    slots, entries, _ = await _build_entry_views(db, school_id, tt, class_group_id=class_group_id)
    return TimetableGridResponse(
        timetable=TimetableResponse.model_validate(tt),
        slots=slots,
        entries=entries,
    )


async def get_teacher_timetable(
    db: AsyncSession, school_id: UUID, timetable_id: UUID, teacher_id: UUID
) -> TimetableGridResponse:
    tt = await _get_timetable_or_404(db, school_id, timetable_id)
    slots, entries, _ = await _build_entry_views(db, school_id, tt, teacher_id=teacher_id)
    return TimetableGridResponse(
        timetable=TimetableResponse.model_validate(tt),
        slots=slots,
        entries=entries,
    )


async def get_master_timetable(
    db: AsyncSession, school_id: UUID, timetable_id: UUID
) -> TimetableMasterGridResponse:
    tt = await _get_timetable_or_404(db, school_id, timetable_id)
    slots, entries, cg_summaries = await _build_entry_views(db, school_id, tt)
    return TimetableMasterGridResponse(
        timetable=TimetableResponse.model_validate(tt),
        class_groups=cg_summaries,
        slots=slots,
        entries=entries,
    )


# ---------------------------------------------------------------------------
# $0 Natural Language Constraint Assistant (Deterministic Intent Parser)
# ---------------------------------------------------------------------------

_DAY_MAP = {
    "monday": 1,
    "mon": 1,
    "tuesday": 2,
    "tue": 2,
    "wednesday": 3,
    "wed": 3,
    "thursday": 4,
    "thu": 4,
    "friday": 5,
    "fri": 5,
    "saturday": 6,
    "sat": 6,
}


async def parse_natural_language_constraint(
    db: AsyncSession, school_id: UUID, academic_session_id: UUID, prompt: str
) -> AssistantActionPreview:
    lower = prompt.strip().lower()

    # 1. Morning preference for subject(s)
    if "morning" in lower and (
        "put" in lower or "prefer" in lower or "schedule" in lower or "before break" in lower
    ):
        subjects = (
            (
                await db.execute(
                    select(Subject).where(
                        Subject.school_id == school_id, Subject.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        matched_subjs = [
            s
            for s in subjects
            if s.name.lower() in lower or s.code.lower() in re.findall(r"\b[a-z]{2,5}\b", lower)
        ]
        if matched_subjs:
            subj = matched_subjs[0]
            return AssistantActionPreview(
                action_type="UPSERT_SUBJECT_RULE",
                summary=f"Prefer morning slots for {subj.name} ({subj.code}).",
                confidence="HIGH",
                payload={
                    "academic_session_id": str(academic_session_id),
                    "subject_id": str(subj.id),
                    "prefer_morning": True,
                    "periods_per_week": 4 if subj.code in ("ENG", "MTH") else 2,
                    "double_periods_per_week": 1 if subj.code in ("ENG", "MTH") else 0,
                },
            )

    # 2. Teacher unavailable on a day / max periods per day
    users = (
        (
            await db.execute(
                select(User).where(
                    User.school_id == school_id, User.is_active.is_(True), User.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    matched_teacher = next(
        (u for u in users if u.first_name.lower() in lower or u.last_name.lower() in lower),
        None,
    )
    if matched_teacher is not None:
        matched_day = next(
            (num for name, num in _DAY_MAP.items() if re.search(rf"\b{name}\b", lower)), None
        )
        max_match = re.search(r"(?:max|maximum|more than|at most)\s+(\d+)\s+period", lower)
        if matched_day is not None and (
            "not" in lower
            or "shouldn't" in lower
            or "unavailable" in lower
            or "off" in lower
            or "can't" in lower
        ):
            day_label = next(
                k.capitalize() for k, v in _DAY_MAP.items() if v == matched_day and len(k) > 3
            )
            return AssistantActionPreview(
                action_type="CREATE_TEACHER_AVAILABILITY",
                summary=(
                    f"Mark {matched_teacher.first_name} {matched_teacher.last_name} "
                    f"as unavailable on {day_label}s."
                ),
                confidence="HIGH",
                payload={
                    "academic_session_id": str(academic_session_id),
                    "teacher_id": str(matched_teacher.id),
                    "day_of_week": matched_day,
                    "availability_type": "UNAVAILABLE",
                },
            )
        if max_match:
            cap = int(max_match.group(1))
            return AssistantActionPreview(
                action_type="CREATE_TEACHER_AVAILABILITY",
                summary=(
                    f"Cap {matched_teacher.first_name} {matched_teacher.last_name} "
                    f"to at most {cap} periods per day."
                ),
                confidence="HIGH",
                payload={
                    "academic_session_id": str(academic_session_id),
                    "teacher_id": str(matched_teacher.id),
                    "availability_type": "LOAD_CAP",
                    "max_periods_per_day": cap,
                },
            )

    return AssistantActionPreview(
        action_type="UNSUPPORTED",
        summary=(
            "I couldn't confidently match a teacher, subject, or day in that request. "
            "Try e.g. 'Mr. Ade shouldn't teach on Friday', 'Mrs. Bello max 4 periods per day', "
            "or 'Put Mathematics in the morning'."
        ),
        confidence="LOW",
        warnings=["Use the Rules or Teacher Availability tabs for custom configurations."],
    )
