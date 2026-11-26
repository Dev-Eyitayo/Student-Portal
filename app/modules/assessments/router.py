from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_authenticated_user, get_tenant_db
from app.modules.assessments import services
from app.modules.assessments.schemas import (
    AssessmentDefinitionBulkActionResponse,
    AssessmentDefinitionBulkCreateRequest,
    AssessmentDefinitionBulkRemoveRequest,
    AssessmentDefinitionBulkUpdateRequest,
    AssessmentDefinitionCreateRequest,
    AssessmentDefinitionResponse,
    AssessmentDefinitionUpdateRequest,
    BulkScoreEntryRequest,
    BulkScoreEntryResponse,
    ScoreSheetActionRequest,
    ScoreSheetOverviewItem,
    ScoreSheetOverviewPageResponse,
    ScoreSheetOverviewSummary,
    ScoreSheetResponse,
    ScoreSheetStatusResponse,
    ScoreSheetUnlockRequest,
)
from app.modules.audit.services import AuditContext
from app.modules.common.schemas import MessageResponse, Page, SuccessResponse
from app.modules.identity.models import User

router = APIRouter(prefix="/assessments", tags=["Assessments & Scores"])


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


# --------------------------------------------------------------------------
# Assessment definitions
# --------------------------------------------------------------------------


@router.get(
    "/definitions",
    response_model=SuccessResponse[Page[AssessmentDefinitionResponse]],
)
async def list_definitions(
    academic_term_id: UUID | None = Query(None),
    class_subject_id: UUID | None = Query(None),
    academic_level_id: UUID | None = Query(None),
    level_category: str | None = Query(
        None, pattern="^(PRIMARY|JUNIOR_SECONDARY|SENIOR_SECONDARY)$"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_definitions(
        db,
        _school_id(current_user),
        current_user,
        academic_term_id=academic_term_id,
        class_subject_id=class_subject_id,
        academic_level_id=academic_level_id,
        level_category=level_category,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse[Page[AssessmentDefinitionResponse]](
        data=Page[AssessmentDefinitionResponse](items=items, meta=meta)
    )


@router.post(
    "/definitions",
    response_model=SuccessResponse[AssessmentDefinitionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_definition(
    req: AssessmentDefinitionCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    definition = await services.create_definition(
        db, _school_id(current_user), req, current_user, _audit_context(request, current_user)
    )
    return SuccessResponse[AssessmentDefinitionResponse](
        data=AssessmentDefinitionResponse.model_validate(definition)
    )


@router.get(
    "/definitions/{definition_id}",
    response_model=SuccessResponse[AssessmentDefinitionResponse],
)
async def get_definition(
    definition_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    definition = await services.get_definition(
        db, _school_id(current_user), definition_id, current_user
    )
    return SuccessResponse[AssessmentDefinitionResponse](
        data=AssessmentDefinitionResponse.model_validate(definition)
    )


@router.post(
    "/definitions/bulk",
    response_model=SuccessResponse[AssessmentDefinitionBulkActionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def bulk_create_definitions(
    req: AssessmentDefinitionBulkCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await services.bulk_create_definitions(
        db, _school_id(current_user), req, current_user, _audit_context(request, current_user)
    )
    return SuccessResponse[AssessmentDefinitionBulkActionResponse](data=result)


@router.post(
    "/definitions/bulk-remove",
    response_model=SuccessResponse[AssessmentDefinitionBulkActionResponse],
)
async def bulk_remove_definitions(
    req: AssessmentDefinitionBulkRemoveRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await services.bulk_remove_definitions(
        db, _school_id(current_user), req, current_user, _audit_context(request, current_user)
    )
    return SuccessResponse[AssessmentDefinitionBulkActionResponse](data=result)


@router.post(
    "/definitions/bulk-update",
    response_model=SuccessResponse[AssessmentDefinitionBulkActionResponse],
)
async def bulk_update_definitions(
    req: AssessmentDefinitionBulkUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await services.bulk_update_definitions(
        db, _school_id(current_user), req, current_user, _audit_context(request, current_user)
    )
    return SuccessResponse[AssessmentDefinitionBulkActionResponse](data=result)


@router.delete(
    "/definitions/{definition_id}",
    response_model=SuccessResponse[MessageResponse],
)
async def delete_definition(
    definition_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    msg = await services.delete_definition(
        db,
        _school_id(current_user),
        definition_id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](data=MessageResponse(message=msg))


@router.patch(
    "/definitions/{definition_id}",
    response_model=SuccessResponse[AssessmentDefinitionResponse],
)
async def update_definition(
    definition_id: UUID,
    req: AssessmentDefinitionUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    definition = await services.update_definition(
        db,
        _school_id(current_user),
        definition_id,
        req,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[AssessmentDefinitionResponse](
        data=AssessmentDefinitionResponse.model_validate(definition)
    )


# --------------------------------------------------------------------------
# Score sheet read + bulk entry
# --------------------------------------------------------------------------


@router.get(
    "/sheets",
    response_model=SuccessResponse[ScoreSheetOverviewPageResponse],
)
async def list_score_sheets_overview(
    academic_term_id: UUID = Query(...),
    class_group_id: UUID | None = Query(None),
    subject_id: UUID | None = Query(None),
    sheet_status: str | None = Query(None, alias="status", max_length=16),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta, summary = await services.list_score_sheets_overview(
        db,
        _school_id(current_user),
        academic_term_id,
        current_user,
        class_group_id=class_group_id,
        subject_id=subject_id,
        status_filter=sheet_status,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse[ScoreSheetOverviewPageResponse](
        data=ScoreSheetOverviewPageResponse(
            items=[ScoreSheetOverviewItem.model_validate(item) for item in items],
            meta=meta,
            summary=ScoreSheetOverviewSummary.model_validate(summary),
        )
    )


@router.get(
    "/scores",
    response_model=SuccessResponse[ScoreSheetResponse],
)
async def get_score_sheet(
    class_subject_id: UUID,
    assessment_definition_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    # Authorization is resource-scoped in services.get_score_sheet, not a router dependency.
    definition, rows = await services.get_score_sheet(
        db, _school_id(current_user), class_subject_id, assessment_definition_id, current_user
    )
    return SuccessResponse[ScoreSheetResponse](
        data=ScoreSheetResponse(
            assessment_definition=AssessmentDefinitionResponse.model_validate(definition),
            rows=rows,
        )
    )


@router.post(
    "/scores/bulk-entry",
    response_model=SuccessResponse[BulkScoreEntryResponse],
)
async def bulk_score_entry(
    req: BulkScoreEntryRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await services.bulk_score_entry(
        db, _school_id(current_user), req, current_user, _audit_context(request, current_user)
    )
    return SuccessResponse[BulkScoreEntryResponse](data=result)


# --------------------------------------------------------------------------
# Score sheet lifecycle
# --------------------------------------------------------------------------


@router.post(
    "/submit",
    response_model=SuccessResponse[ScoreSheetStatusResponse],
)
async def submit_score_sheet(
    req: ScoreSheetActionRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    sheet_status = await services.submit_score_sheet(
        db,
        _school_id(current_user),
        req.academic_term_id,
        req.class_subject_id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[ScoreSheetStatusResponse](
        data=ScoreSheetStatusResponse.model_validate(sheet_status)
    )


@router.post(
    "/approve",
    response_model=SuccessResponse[ScoreSheetStatusResponse],
)
async def approve_score_sheet(
    req: ScoreSheetActionRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    sheet_status = await services.approve_score_sheet(
        db,
        _school_id(current_user),
        req.academic_term_id,
        req.class_subject_id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[ScoreSheetStatusResponse](
        data=ScoreSheetStatusResponse.model_validate(sheet_status)
    )


@router.post(
    "/unlock",
    response_model=SuccessResponse[ScoreSheetStatusResponse],
)
async def unlock_score_sheet(
    req: ScoreSheetUnlockRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    sheet_status = await services.unlock_score_sheet(
        db,
        _school_id(current_user),
        req.academic_term_id,
        req.class_subject_id,
        req.reason,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[ScoreSheetStatusResponse](
        data=ScoreSheetStatusResponse.model_validate(sheet_status)
    )


@router.get("/mark-book/export")
async def export_mark_book(
    class_subject_id: UUID | None = Query(None),
    all_arms: bool = Query(False),
    class_group_id: UUID | None = Query(None),
    academic_session_id: UUID | None = Query(None),
    subject_id: UUID | None = Query(None),
    academic_level_id: UUID | None = Query(None),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    file_bytes, filename, media_type = await services.export_mark_book(
        db,
        _school_id(current_user),
        current_user,
        class_subject_id=class_subject_id,
        all_arms=all_arms,
        class_group_id=class_group_id,
        academic_session_id=academic_session_id,
        subject_id=subject_id,
        academic_level_id=academic_level_id,
    )
    return StreamingResponse(
        BytesIO(file_bytes),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
