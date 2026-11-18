from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    build_audit_context,
    get_current_authenticated_user,
    get_tenant_db,
    require_school_id,
)
from app.core.permissions import require_permission
from app.modules.common.schemas import SuccessResponse
from app.modules.computation.schemas import PublishTermRequest, PublishTermResponse
from app.modules.identity.models import User
from app.modules.publication.services import publish_term

router = APIRouter(
    prefix="/publication",
    tags=["Publication & Result Access"],
    dependencies=[Depends(require_permission("reports:publish"))],
)


@router.post(
    "/publish-term",
    response_model=SuccessResponse[PublishTermResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def publish_term_route(
    req: PublishTermRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    data = await publish_term(
        db,
        require_school_id(current_user),
        req,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[PublishTermResponse](data=data)
