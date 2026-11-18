from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    build_audit_context,
    get_current_authenticated_user,
    get_tenant_db,
    require_school_id,
)
from app.modules.common.schemas import SuccessResponse
from app.modules.computation import services
from app.modules.computation.schemas import (
    BroadsheetResponse,
    CalculateTermRequest,
    CalculateTermResponse,
)
from app.modules.identity.models import User

router = APIRouter(prefix="/computation", tags=["Computation & Broadsheets"])


@router.post(
    "/calculate-term",
    response_model=SuccessResponse[CalculateTermResponse],
)
async def calculate_term(
    req: CalculateTermRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    data = await services.calculate_term(
        db,
        require_school_id(current_user),
        req,
        current_user,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[CalculateTermResponse](data=data)


@router.get(
    "/broadsheet/{class_group_id}",
    response_model=SuccessResponse[BroadsheetResponse],
)
async def get_broadsheet(
    class_group_id: UUID,
    academic_term_id: UUID | None = Query(None),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    data = await services.get_broadsheet(
        db,
        require_school_id(current_user),
        class_group_id,
        academic_term_id,
        current_user,
    )
    return SuccessResponse[BroadsheetResponse](data=data)
