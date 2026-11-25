from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.dependencies import (
    get_current_authenticated_user,
    get_tenant_db,
    require_school_id,
)
from app.core.permissions import require_permission
from app.modules.audit.services import AuditContext
from app.modules.common.schemas import SuccessResponse
from app.modules.identity.models import User
from app.modules.timetable import services
from app.modules.timetable.schemas import (
    ApplyRelaxationRequest,
    AssistantActionPreview,
    AssistantParseRequest,
    BellScheduleCreateRequest,
    BellScheduleResponse,
    BellScheduleSlotsReplaceRequest,
    BellScheduleUpdateRequest,
    RelaxationSuggestion,
    RoomCreateRequest,
    RoomResponse,
    RoomUpdateRequest,
    SubjectSchedulingRuleResponse,
    SubjectSchedulingRuleUpsertRequest,
    SyncGroupCreateRequest,
    SyncGroupResponse,
    SyncGroupUpdateRequest,
    TeacherAvailabilityResponse,
    TeacherAvailabilityUpsertRequest,
    TimetableCreateRequest,
    TimetableEntryMoveValidateRequest,
    TimetableEntryMoveValidateResponse,
    TimetableEntryUpdateRequest,
    TimetableGenerateResponse,
    TimetableGridResponse,
    TimetableJobStatusResponse,
    TimetableMasterGridResponse,
    TimetableResponse,
)
from app.modules.timetable.tasks import generate_timetable_task

router = APIRouter(prefix="/timetable", tags=["Timetable Scheduling"])


def _audit(current_user: User, request: Request) -> AuditContext:
    return AuditContext(
        actor_id=current_user.id,
        school_id=require_school_id(current_user),
        actor_ip=request.client.host if request.client else "unknown",
    )


# ---------------------------------------------------------------------------
# Bell Schedules
# ---------------------------------------------------------------------------


@router.get("/bell-schedules", response_model=SuccessResponse[list[BellScheduleResponse]])
async def list_bell_schedules(
    academic_session_id: UUID | None = Query(None),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    data = await services.list_bell_schedules(
        db, require_school_id(current_user), academic_session_id
    )
    return SuccessResponse[list[BellScheduleResponse]](data=data)


@router.post(
    "/bell-schedules",
    response_model=SuccessResponse[BellScheduleResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def create_bell_schedule(
    req: BellScheduleCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    data = await services.create_bell_schedule(
        db, require_school_id(current_user), req, _audit(current_user, request)
    )
    return SuccessResponse[BellScheduleResponse](data=data)


@router.get("/bell-schedules/{schedule_id}", response_model=SuccessResponse[BellScheduleResponse])
async def get_bell_schedule(
    schedule_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    data = await services.get_bell_schedule(db, require_school_id(current_user), schedule_id)
    return SuccessResponse[BellScheduleResponse](data=data)


@router.patch(
    "/bell-schedules/{schedule_id}",
    response_model=SuccessResponse[BellScheduleResponse],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def update_bell_schedule(
    schedule_id: UUID,
    req: BellScheduleUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    data = await services.update_bell_schedule(
        db, require_school_id(current_user), schedule_id, req, _audit(current_user, request)
    )
    return SuccessResponse[BellScheduleResponse](data=data)


@router.delete(
    "/bell-schedules/{schedule_id}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def delete_bell_schedule(
    schedule_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.delete_bell_schedule(
        db, require_school_id(current_user), schedule_id, _audit(current_user, request)
    )
    return SuccessResponse[dict](data={"deleted": True})


@router.put(
    "/bell-schedules/{schedule_id}/slots",
    response_model=SuccessResponse[BellScheduleResponse],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def replace_bell_schedule_slots(
    schedule_id: UUID,
    req: BellScheduleSlotsReplaceRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    data = await services.replace_bell_schedule_slots(
        db, require_school_id(current_user), schedule_id, req, _audit(current_user, request)
    )
    return SuccessResponse[BellScheduleResponse](data=data)


# ---------------------------------------------------------------------------
# Shared Facilities / Rooms (Opt-in)
# ---------------------------------------------------------------------------


@router.get("/rooms", response_model=SuccessResponse[list[RoomResponse]])
async def list_rooms(
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[list[RoomResponse]](
        data=await services.list_rooms(db, require_school_id(current_user))
    )


@router.post(
    "/rooms",
    response_model=SuccessResponse[RoomResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def create_room(
    req: RoomCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[RoomResponse](
        data=await services.create_room(
            db, require_school_id(current_user), req, _audit(current_user, request)
        )
    )


@router.patch(
    "/rooms/{room_id}",
    response_model=SuccessResponse[RoomResponse],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def update_room(
    room_id: UUID,
    req: RoomUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[RoomResponse](
        data=await services.update_room(
            db, require_school_id(current_user), room_id, req, _audit(current_user, request)
        )
    )


@router.delete(
    "/rooms/{room_id}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def delete_room(
    room_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.delete_room(
        db, require_school_id(current_user), room_id, _audit(current_user, request)
    )
    return SuccessResponse[dict](data={"deleted": True})


# ---------------------------------------------------------------------------
# Rules, Sync Groups & Teacher Availabilities
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=SuccessResponse[list[SubjectSchedulingRuleResponse]])
async def list_rules(
    academic_session_id: UUID | None = Query(None),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[list[SubjectSchedulingRuleResponse]](
        data=await services.list_scheduling_rules(
            db, require_school_id(current_user), academic_session_id
        )
    )


@router.put(
    "/rules",
    response_model=SuccessResponse[SubjectSchedulingRuleResponse],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def upsert_rule(
    req: SubjectSchedulingRuleUpsertRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[SubjectSchedulingRuleResponse](
        data=await services.upsert_scheduling_rule(
            db, require_school_id(current_user), req, _audit(current_user, request)
        )
    )


@router.delete(
    "/rules/{rule_id}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def delete_rule(
    rule_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.delete_scheduling_rule(
        db, require_school_id(current_user), rule_id, _audit(current_user, request)
    )
    return SuccessResponse[dict](data={"deleted": True})


@router.get("/sync-groups", response_model=SuccessResponse[list[SyncGroupResponse]])
async def list_sync_groups(
    academic_session_id: UUID | None = Query(None),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[list[SyncGroupResponse]](
        data=await services.list_sync_groups(
            db, require_school_id(current_user), academic_session_id
        )
    )


@router.post(
    "/sync-groups",
    response_model=SuccessResponse[SyncGroupResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def create_sync_group(
    req: SyncGroupCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[SyncGroupResponse](
        data=await services.create_sync_group(
            db, require_school_id(current_user), req, _audit(current_user, request)
        )
    )


@router.patch(
    "/sync-groups/{sync_group_id}",
    response_model=SuccessResponse[SyncGroupResponse],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def update_sync_group(
    sync_group_id: UUID,
    req: SyncGroupUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[SyncGroupResponse](
        data=await services.update_sync_group(
            db, require_school_id(current_user), sync_group_id, req, _audit(current_user, request)
        )
    )


@router.delete(
    "/sync-groups/{sync_group_id}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def delete_sync_group(
    sync_group_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.delete_sync_group(
        db, require_school_id(current_user), sync_group_id, _audit(current_user, request)
    )
    return SuccessResponse[dict](data={"deleted": True})


@router.get(
    "/teacher-availabilities", response_model=SuccessResponse[list[TeacherAvailabilityResponse]]
)
async def list_teacher_availabilities(
    academic_session_id: UUID | None = Query(None),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[list[TeacherAvailabilityResponse]](
        data=await services.list_teacher_availabilities(
            db, require_school_id(current_user), academic_session_id
        )
    )


@router.put(
    "/teacher-availabilities",
    response_model=SuccessResponse[TeacherAvailabilityResponse],
    dependencies=[Depends(require_permission("teachers:assign"))],
)
async def upsert_teacher_availability(
    req: TeacherAvailabilityUpsertRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[TeacherAvailabilityResponse](
        data=await services.upsert_teacher_availability(
            db, require_school_id(current_user), req, _audit(current_user, request)
        )
    )


@router.delete(
    "/teacher-availabilities/{availability_id}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission("teachers:assign"))],
)
async def delete_teacher_availability(
    availability_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.delete_teacher_availability(
        db, require_school_id(current_user), availability_id, _audit(current_user, request)
    )
    return SuccessResponse[dict](data={"deleted": True})


# ---------------------------------------------------------------------------
# Schedules (Timetables), Async Generation, Manual Edits & Views
# ---------------------------------------------------------------------------


@router.get("/schedules", response_model=SuccessResponse[list[TimetableResponse]])
async def list_schedules(
    academic_term_id: UUID | None = Query(None),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[list[TimetableResponse]](
        data=await services.list_timetables(db, require_school_id(current_user), academic_term_id)
    )


@router.post(
    "/schedules",
    response_model=SuccessResponse[TimetableResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def create_schedule(
    req: TimetableCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[TimetableResponse](
        data=await services.create_timetable(
            db, require_school_id(current_user), req, _audit(current_user, request)
        )
    )


@router.get("/schedules/{timetable_id}", response_model=SuccessResponse[TimetableResponse])
async def get_schedule(
    timetable_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[TimetableResponse](
        data=await services.get_timetable(db, require_school_id(current_user), timetable_id)
    )


@router.delete(
    "/schedules/{timetable_id}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def delete_schedule(
    timetable_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.delete_timetable(
        db, require_school_id(current_user), timetable_id, _audit(current_user, request)
    )
    return SuccessResponse[dict](data={"deleted": True})


@router.post(
    "/schedules/{timetable_id}/generate",
    response_model=SuccessResponse[TimetableGenerateResponse],
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def generate_schedule(
    timetable_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    school_id = require_school_id(current_user)
    tt = await services._get_timetable_or_404(db, school_id, timetable_id)
    if celery_app.conf.task_always_eager:
        await services.run_timetable_generation(db, school_id, tt.id)
        task_id = f"eager-{tt.id}"
    else:
        tt.generation_status = "QUEUED"
        await db.commit()
        task = generate_timetable_task.delay(str(school_id), str(tt.id))
        tt.celery_task_id = task.id
        await db.commit()
        task_id = task.id

    return SuccessResponse[TimetableGenerateResponse](
        data=TimetableGenerateResponse(
            timetable_id=tt.id,
            task_id=task_id,
            generation_status=tt.generation_status,
        )
    )


@router.get(
    "/schedules/{timetable_id}/jobs/{task_id}",
    response_model=SuccessResponse[TimetableJobStatusResponse],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def get_generation_job_status(
    timetable_id: UUID,
    task_id: str,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    tt = await services.get_timetable(db, require_school_id(current_user), timetable_id)
    async_res = AsyncResult(task_id, app=celery_app)
    return SuccessResponse[TimetableJobStatusResponse](
        data=TimetableJobStatusResponse(
            timetable_id=tt.id,
            task_id=task_id,
            state=async_res.state,
            generation_status=tt.generation_status,
            generation_summary=tt.generation_summary,
        )
    )


@router.get(
    "/schedules/{timetable_id}/relaxation-suggestions",
    response_model=SuccessResponse[list[RelaxationSuggestion]],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def get_relaxation_suggestions(
    timetable_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    school_id = require_school_id(current_user)
    tt = await services._get_timetable_or_404(db, school_id, timetable_id)
    suggestions = await services.build_relaxation_suggestions(db, school_id, tt)
    return SuccessResponse[list[RelaxationSuggestion]](data=suggestions)


@router.post(
    "/schedules/{timetable_id}/relaxation-suggestions/apply",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def apply_relaxation_suggestion(
    timetable_id: UUID,
    req: ApplyRelaxationRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    school_id = require_school_id(current_user)
    await services._get_timetable_or_404(db, school_id, timetable_id)
    result = await services.apply_relaxation(db, school_id, req, _audit(current_user, request))
    return SuccessResponse[dict](data=result)


@router.post(
    "/schedules/{timetable_id}/entries/{entry_id}/validate",
    response_model=SuccessResponse[TimetableEntryMoveValidateResponse],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def validate_entry_move(
    timetable_id: UUID,
    entry_id: UUID,
    req: TimetableEntryMoveValidateRequest,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[TimetableEntryMoveValidateResponse](
        data=await services.validate_entry_move(
            db, require_school_id(current_user), timetable_id, entry_id, req
        )
    )


@router.patch(
    "/schedules/{timetable_id}/entries/{entry_id}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def update_entry(
    timetable_id: UUID,
    entry_id: UUID,
    req: TimetableEntryUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    entries = await services.update_timetable_entry(
        db,
        require_school_id(current_user),
        timetable_id,
        entry_id,
        req,
        _audit(current_user, request),
    )
    return SuccessResponse[dict](data={"updated_entry_ids": [str(e.id) for e in entries]})


@router.delete(
    "/schedules/{timetable_id}/entries/{entry_id}",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def delete_entry(
    timetable_id: UUID,
    entry_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.delete_timetable_entry(
        db, require_school_id(current_user), timetable_id, entry_id, _audit(current_user, request)
    )
    return SuccessResponse[dict](data={"deleted": True})


@router.post(
    "/schedules/{timetable_id}/publish",
    response_model=SuccessResponse[TimetableResponse],
    dependencies=[Depends(require_permission("timetable:publish"))],
)
async def publish_schedule(
    timetable_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[TimetableResponse](
        data=await services.publish_timetable(
            db,
            require_school_id(current_user),
            timetable_id,
            current_user,
            _audit(current_user, request),
        )
    )


@router.post(
    "/schedules/{timetable_id}/clone",
    response_model=SuccessResponse[TimetableResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def clone_schedule(
    timetable_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[TimetableResponse](
        data=await services.clone_timetable(
            db, require_school_id(current_user), timetable_id, _audit(current_user, request)
        )
    )


@router.get(
    "/schedules/{timetable_id}/class-groups/{class_group_id}",
    response_model=SuccessResponse[TimetableGridResponse],
)
async def get_class_group_timetable(
    timetable_id: UUID,
    class_group_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[TimetableGridResponse](
        data=await services.get_class_group_timetable(
            db, require_school_id(current_user), timetable_id, class_group_id
        )
    )


@router.get(
    "/schedules/{timetable_id}/teachers/me",
    response_model=SuccessResponse[TimetableGridResponse],
)
async def get_my_teacher_timetable(
    timetable_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[TimetableGridResponse](
        data=await services.get_teacher_timetable(
            db, require_school_id(current_user), timetable_id, current_user.id
        )
    )


@router.get(
    "/schedules/{timetable_id}/master",
    response_model=SuccessResponse[TimetableMasterGridResponse],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def get_master_timetable(
    timetable_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[TimetableMasterGridResponse](
        data=await services.get_master_timetable(db, require_school_id(current_user), timetable_id)
    )


# ---------------------------------------------------------------------------
# $0 Natural Language Constraint Assistant
# ---------------------------------------------------------------------------


@router.post(
    "/assistant/parse",
    response_model=SuccessResponse[AssistantActionPreview],
    dependencies=[Depends(require_permission("timetable:manage"))],
)
async def parse_nl_constraint(
    req: AssistantParseRequest,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    return SuccessResponse[AssistantActionPreview](
        data=await services.parse_natural_language_constraint(
            db, require_school_id(current_user), req.academic_session_id, req.prompt
        )
    )
