from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_authenticated_user, get_tenant_db
from app.core.permissions import require_permission
from app.modules.academic_structure import services
from app.modules.academic_structure.schemas import (
    ArmCreateRequest,
    ArmResponse,
    ArmUpdateRequest,
    ClassGroupCreateRequest,
    ClassGroupResponse,
    ClassGroupUpdateRequest,
    DepartmentCreateRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
    LevelCreateRequest,
    LevelResponse,
    LevelUpdateRequest,
    SessionCreateRequest,
    SessionResponse,
    SessionUpdateRequest,
    TermCreateRequest,
    TermResponse,
    TermUpdateRequest,
)
from app.modules.audit.services import AuditContext
from app.modules.common.schemas import MessageResponse, Page, SuccessResponse
from app.modules.identity.models import User

router = APIRouter(
    prefix="/structure",
    tags=["Academic Structure"],
    dependencies=[Depends(require_permission("structure:manage"))],
)


def _school_id(current_user: User) -> UUID:
    if current_user.school_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not linked to an active school. Please contact your school administrator.",
        )
    return current_user.school_id


def _audit_context(request: Request, current_user: User) -> AuditContext:
    client_ip = request.client.host if request.client else "0.0.0.0"
    return AuditContext(
        school_id=_school_id(current_user),
        actor_id=current_user.id,
        actor_ip=client_ip,
    )


@router.get("/sessions", response_model=SuccessResponse[Page[SessionResponse]])
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_retired: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_sessions(
        db,
        _school_id(current_user),
        page=page,
        page_size=page_size,
        include_retired=include_retired,
    )
    return SuccessResponse[Page[SessionResponse]](
        data=Page[SessionResponse](items=items, meta=meta)
    )


@router.post(
    "/sessions",
    response_model=SuccessResponse[SessionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    req: SessionCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    session = await services.create_session(
        db,
        _school_id(current_user),
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[SessionResponse](data=SessionResponse.model_validate(session))


@router.get("/sessions/{session_id}", response_model=SuccessResponse[SessionResponse])
async def get_session(
    session_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    session = await services.get_session(db, _school_id(current_user), session_id)
    return SuccessResponse[SessionResponse](data=SessionResponse.model_validate(session))


@router.patch("/sessions/{session_id}", response_model=SuccessResponse[SessionResponse])
async def update_session(
    session_id: UUID,
    req: SessionUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    session = await services.update_session(
        db,
        _school_id(current_user),
        session_id,
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[SessionResponse](data=SessionResponse.model_validate(session))


@router.delete("/sessions/{session_id}", response_model=SuccessResponse[MessageResponse])
async def retire_session(
    session_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.retire_session(
        db,
        _school_id(current_user),
        session_id,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(
            message=(
                "The academic session has been retired. Historical records that reference it "
                "remain available."
            )
        )
    )


@router.get(
    "/sessions/{session_id}/terms",
    response_model=SuccessResponse[Page[TermResponse]],
)
async def list_terms(
    session_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_retired: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_terms(
        db,
        _school_id(current_user),
        session_id,
        page=page,
        page_size=page_size,
        include_retired=include_retired,
    )
    return SuccessResponse[Page[TermResponse]](data=Page[TermResponse](items=items, meta=meta))


@router.post(
    "/sessions/{session_id}/terms",
    response_model=SuccessResponse[TermResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_term(
    session_id: UUID,
    req: TermCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    term = await services.create_term(
        db,
        _school_id(current_user),
        session_id,
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[TermResponse](data=TermResponse.model_validate(term))


@router.get("/terms/{term_id}", response_model=SuccessResponse[TermResponse])
async def get_term(
    term_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    term = await services.get_term(db, _school_id(current_user), term_id)
    return SuccessResponse[TermResponse](data=TermResponse.model_validate(term))


@router.patch("/terms/{term_id}", response_model=SuccessResponse[TermResponse])
async def update_term(
    term_id: UUID,
    req: TermUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    term = await services.update_term(
        db,
        _school_id(current_user),
        term_id,
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[TermResponse](data=TermResponse.model_validate(term))


@router.delete("/terms/{term_id}", response_model=SuccessResponse[MessageResponse])
async def retire_term(
    term_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.retire_term(
        db,
        _school_id(current_user),
        term_id,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(
            message=(
                "The academic term has been retired. Historical records that reference it "
                "remain available."
            )
        )
    )


@router.get("/levels", response_model=SuccessResponse[Page[LevelResponse]])
async def list_levels(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_retired: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_levels(
        db,
        _school_id(current_user),
        page=page,
        page_size=page_size,
        include_retired=include_retired,
    )
    return SuccessResponse[Page[LevelResponse]](data=Page[LevelResponse](items=items, meta=meta))


@router.post(
    "/levels",
    response_model=SuccessResponse[LevelResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_level(
    req: LevelCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    level = await services.create_level(
        db,
        _school_id(current_user),
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[LevelResponse](data=LevelResponse.model_validate(level))


@router.get("/levels/{level_id}", response_model=SuccessResponse[LevelResponse])
async def get_level(
    level_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    level = await services.get_level(db, _school_id(current_user), level_id)
    return SuccessResponse[LevelResponse](data=LevelResponse.model_validate(level))


@router.patch("/levels/{level_id}", response_model=SuccessResponse[LevelResponse])
async def update_level(
    level_id: UUID,
    req: LevelUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    level = await services.update_level(
        db,
        _school_id(current_user),
        level_id,
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[LevelResponse](data=LevelResponse.model_validate(level))


@router.delete("/levels/{level_id}", response_model=SuccessResponse[MessageResponse])
async def retire_level(
    level_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.retire_level(
        db,
        _school_id(current_user),
        level_id,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(
            message=(
                "The class level has been retired. Existing historical class records remain "
                "available."
            )
        )
    )


@router.get("/departments", response_model=SuccessResponse[Page[DepartmentResponse]])
async def list_departments(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_retired: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_departments(
        db,
        _school_id(current_user),
        page=page,
        page_size=page_size,
        include_retired=include_retired,
    )
    return SuccessResponse[Page[DepartmentResponse]](
        data=Page[DepartmentResponse](items=items, meta=meta)
    )


@router.post(
    "/departments",
    response_model=SuccessResponse[DepartmentResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_department(
    req: DepartmentCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    department = await services.create_department(
        db,
        _school_id(current_user),
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[DepartmentResponse](data=DepartmentResponse.model_validate(department))


@router.get(
    "/departments/{department_id}",
    response_model=SuccessResponse[DepartmentResponse],
)
async def get_department(
    department_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    department = await services.get_department(db, _school_id(current_user), department_id)
    return SuccessResponse[DepartmentResponse](data=DepartmentResponse.model_validate(department))


@router.patch(
    "/departments/{department_id}",
    response_model=SuccessResponse[DepartmentResponse],
)
async def update_department(
    department_id: UUID,
    req: DepartmentUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    department = await services.update_department(
        db,
        _school_id(current_user),
        department_id,
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[DepartmentResponse](data=DepartmentResponse.model_validate(department))


@router.delete(
    "/departments/{department_id}",
    response_model=SuccessResponse[MessageResponse],
)
async def retire_department(
    department_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.retire_department(
        db,
        _school_id(current_user),
        department_id,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(
            message=(
                "The department has been retired. Existing historical class records remain "
                "available."
            )
        )
    )


@router.get("/arms", response_model=SuccessResponse[Page[ArmResponse]])
async def list_arms(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_retired: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_arms(
        db,
        _school_id(current_user),
        page=page,
        page_size=page_size,
        include_retired=include_retired,
    )
    return SuccessResponse[Page[ArmResponse]](data=Page[ArmResponse](items=items, meta=meta))


@router.post(
    "/arms",
    response_model=SuccessResponse[ArmResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_arm(
    req: ArmCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    arm = await services.create_arm(
        db,
        _school_id(current_user),
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[ArmResponse](data=ArmResponse.model_validate(arm))


@router.get("/arms/{arm_id}", response_model=SuccessResponse[ArmResponse])
async def get_arm(
    arm_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    arm = await services.get_arm(db, _school_id(current_user), arm_id)
    return SuccessResponse[ArmResponse](data=ArmResponse.model_validate(arm))


@router.patch("/arms/{arm_id}", response_model=SuccessResponse[ArmResponse])
async def update_arm(
    arm_id: UUID,
    req: ArmUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    arm = await services.update_arm(
        db,
        _school_id(current_user),
        arm_id,
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[ArmResponse](data=ArmResponse.model_validate(arm))


@router.delete("/arms/{arm_id}", response_model=SuccessResponse[MessageResponse])
async def retire_arm(
    arm_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.retire_arm(
        db,
        _school_id(current_user),
        arm_id,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(
            message=(
                "The class arm has been retired. Existing historical class records remain "
                "available."
            )
        )
    )


@router.get("/class-groups", response_model=SuccessResponse[Page[ClassGroupResponse]])
async def list_class_groups(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session_id: UUID | None = Query(None),
    level_id: UUID | None = Query(None),
    department_id: UUID | None = Query(None),
    include_retired: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_class_groups(
        db,
        _school_id(current_user),
        session_id=session_id,
        level_id=level_id,
        department_id=department_id,
        page=page,
        page_size=page_size,
        include_retired=include_retired,
    )
    return SuccessResponse[Page[ClassGroupResponse]](
        data=Page[ClassGroupResponse](items=items, meta=meta)
    )


@router.post(
    "/class-groups",
    response_model=SuccessResponse[ClassGroupResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_class_group(
    req: ClassGroupCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    class_group = await services.create_class_group(
        db,
        _school_id(current_user),
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[ClassGroupResponse](data=ClassGroupResponse.model_validate(class_group))


@router.get(
    "/class-groups/{class_group_id}",
    response_model=SuccessResponse[ClassGroupResponse],
)
async def get_class_group(
    class_group_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    class_group = await services.get_class_group(db, _school_id(current_user), class_group_id)
    return SuccessResponse[ClassGroupResponse](data=ClassGroupResponse.model_validate(class_group))


@router.patch(
    "/class-groups/{class_group_id}",
    response_model=SuccessResponse[ClassGroupResponse],
)
async def update_class_group(
    class_group_id: UUID,
    req: ClassGroupUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    class_group = await services.update_class_group(
        db,
        _school_id(current_user),
        class_group_id,
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[ClassGroupResponse](data=ClassGroupResponse.model_validate(class_group))


@router.delete(
    "/class-groups/{class_group_id}",
    response_model=SuccessResponse[MessageResponse],
)
async def retire_class_group(
    class_group_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.retire_class_group(
        db,
        _school_id(current_user),
        class_group_id,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(
            message=(
                "The class group has been retired. Historical enrollment records that reference "
                "it remain available."
            )
        )
    )
