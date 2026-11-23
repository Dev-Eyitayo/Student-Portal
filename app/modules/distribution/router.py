from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    build_audit_context,
    get_current_authenticated_user,
    get_db,
    get_tenant_db,
    require_school_id,
)
from app.core.permissions import require_permission
from app.modules.common.schemas import SuccessResponse
from app.modules.distribution import services
from app.modules.distribution.schemas import (
    BulkReportCardRemarksRequest,
    BulkReportCardRemarksResponse,
    NotifyTermResultsRequest,
    NotifyTermResultsResponse,
    QrVerifyResponse,
    ReportCardListResponse,
    ReportCardRemarksResponse,
    ReportCardRemarksUpdateRequest,
    VerifiedReportCardResponse,
)
from app.modules.identity.models import User

results_router = APIRouter(prefix="/results", tags=["Result Verification & Delivery"])
publication_distribution_router = APIRouter(
    prefix="/publication", tags=["Result Publication & Distribution"]
)


def _client_ip(request: Request) -> str | None:
    if request.client:
        return request.client.host
    return None


@results_router.get(
    "/verify/{token}",
    response_model=SuccessResponse[VerifiedReportCardResponse],
)
async def verify_result_token(
    token: str,
    request: Request,
    dob: date | None = Query(None),
    admission_number: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    payload = await services.verify_result_token(
        db,
        token,
        dob=dob,
        admission_number=admission_number,
        client_ip=_client_ip(request),
    )
    return SuccessResponse[VerifiedReportCardResponse](data=payload)


@results_router.get(
    "/verify-qr",
    response_model=SuccessResponse[QrVerifyResponse],
)
async def verify_qr(payload: str = Query(...)):
    result = services.verify_qr_payload(payload)
    return SuccessResponse[QrVerifyResponse](data=result)


@publication_distribution_router.get(
    "/report-cards",
    response_model=SuccessResponse[ReportCardListResponse],
)
async def list_report_cards(
    academic_term_id: UUID = Query(...),
    class_group_id: UUID | None = Query(None),
    academic_level_id: UUID | None = Query(None),
    department_id: UUID | None = Query(None),
    promotion_decision: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await services.list_report_cards(
        db,
        require_school_id(current_user),
        academic_term_id,
        class_group_id,
        current_user,
        academic_level_id=academic_level_id,
        department_id=department_id,
        promotion_decision=promotion_decision,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse[ReportCardListResponse](data=result)


@publication_distribution_router.post(
    "/report-cards/bulk-remarks",
    response_model=SuccessResponse[BulkReportCardRemarksResponse],
)
async def bulk_update_report_card_remarks(
    req: BulkReportCardRemarksRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await services.bulk_update_report_card_remarks(
        db,
        require_school_id(current_user),
        req,
        current_user,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[BulkReportCardRemarksResponse](data=result)


@publication_distribution_router.patch(
    "/report-cards/{report_card_id}/remarks",
    response_model=SuccessResponse[ReportCardRemarksResponse],
)
async def update_report_card_remarks(
    report_card_id: UUID,
    req: ReportCardRemarksUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await services.update_report_card_remarks(
        db,
        require_school_id(current_user),
        report_card_id,
        req,
        current_user,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[ReportCardRemarksResponse](data=result)


@publication_distribution_router.post(
    "/notify-term",
    response_model=SuccessResponse[NotifyTermResultsResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def notify_term_results(
    req: NotifyTermResultsRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    _: bool = Depends(require_permission("reports:publish")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await services.queue_term_result_notifications(
        db,
        require_school_id(current_user),
        req,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[NotifyTermResultsResponse](data=result)
