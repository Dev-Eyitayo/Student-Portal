from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    build_audit_context,
    get_current_authenticated_user,
    get_tenant_db,
    require_school_id,
)
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
    StructureTemplateApplyRequest,
    StructureTemplateApplyResponse,
    TermCreateRequest,
    TermResponse,
    TermUpdateRequest,
)
from app.modules.common.schemas import MessageResponse, Page, SuccessResponse
from app.modules.identity.models import User

router = APIRouter(
    prefix="/structure",
    tags=["Academic Structure"],
    dependencies=[Depends(require_permission("structure:manage"))],
)

# No blanket dependency: class-group auth is resource-scoped in the service layer.
class_groups_router = APIRouter(
    prefix="/structure/class-groups",
    tags=["Academic Structure"],
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
        require_school_id(current_user),
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
        require_school_id(current_user),
        req,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[SessionResponse](data=SessionResponse.model_validate(session))


@router.get("/sessions/{session_id}", response_model=SuccessResponse[SessionResponse])
async def get_session(
    session_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    session = await services.get_session(db, require_school_id(current_user), session_id)
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
        require_school_id(current_user),
        session_id,
        req,
        build_audit_context(request, current_user),
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
        require_school_id(current_user),
        session_id,
        build_audit_context(request, current_user),
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
        require_school_id(current_user),
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
        require_school_id(current_user),
        session_id,
        req,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[TermResponse](data=TermResponse.model_validate(term))


@router.get("/terms/{term_id}", response_model=SuccessResponse[TermResponse])
async def get_term(
    term_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    term = await services.get_term(db, require_school_id(current_user), term_id)
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
        require_school_id(current_user),
        term_id,
        req,
        build_audit_context(request, current_user),
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
        require_school_id(current_user),
        term_id,
        build_audit_context(request, current_user),
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
        require_school_id(current_user),
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
        require_school_id(current_user),
        req,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[LevelResponse](data=LevelResponse.model_validate(level))


@router.get("/levels/{level_id}", response_model=SuccessResponse[LevelResponse])
async def get_level(
    level_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    level = await services.get_level(db, require_school_id(current_user), level_id)
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
        require_school_id(current_user),
        level_id,
        req,
        build_audit_context(request, current_user),
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
        require_school_id(current_user),
        level_id,
        build_audit_context(request, current_user),
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
        require_school_id(current_user),
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
        require_school_id(current_user),
        req,
        build_audit_context(request, current_user),
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
    department = await services.get_department(db, require_school_id(current_user), department_id)
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
        require_school_id(current_user),
        department_id,
        req,
        build_audit_context(request, current_user),
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
        require_school_id(current_user),
        department_id,
        build_audit_context(request, current_user),
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
        require_school_id(current_user),
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
        require_school_id(current_user),
        req,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[ArmResponse](data=ArmResponse.model_validate(arm))


@router.get("/arms/{arm_id}", response_model=SuccessResponse[ArmResponse])
async def get_arm(
    arm_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    arm = await services.get_arm(db, require_school_id(current_user), arm_id)
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
        require_school_id(current_user),
        arm_id,
        req,
        build_audit_context(request, current_user),
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
        require_school_id(current_user),
        arm_id,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(
            message=(
                "The class arm has been retired. Existing historical class records remain "
                "available."
            )
        )
    )


@class_groups_router.get(
    "",
    response_model=SuccessResponse[Page[ClassGroupResponse]],
)
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
        require_school_id(current_user),
        current_user,
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


@class_groups_router.post(
    "",
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
        require_school_id(current_user),
        req,
        current_user,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[ClassGroupResponse](data=ClassGroupResponse.model_validate(class_group))


@class_groups_router.get(
    "/{class_group_id}",
    response_model=SuccessResponse[ClassGroupResponse],
)
async def get_class_group(
    class_group_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    class_group = await services.get_class_group(
        db, require_school_id(current_user), class_group_id, current_user
    )
    return SuccessResponse[ClassGroupResponse](data=ClassGroupResponse.model_validate(class_group))


@class_groups_router.patch(
    "/{class_group_id}",
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
        require_school_id(current_user),
        class_group_id,
        req,
        current_user,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[ClassGroupResponse](data=ClassGroupResponse.model_validate(class_group))


@class_groups_router.delete(
    "/{class_group_id}",
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
        require_school_id(current_user),
        class_group_id,
        current_user,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(
            message=(
                "The class group has been retired. Historical enrollment records that reference "
                "it remain available."
            )
        )
    )


@router.post(
    "/templates/apply",
    response_model=SuccessResponse[StructureTemplateApplyResponse],
    status_code=status.HTTP_201_CREATED,
)
async def apply_structure_template(
    req: StructureTemplateApplyRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await services.apply_structure_template(
        db,
        require_school_id(current_user),
        req,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[StructureTemplateApplyResponse](data=result)
