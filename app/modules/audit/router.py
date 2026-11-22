from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_authenticated_user, get_tenant_db, require_school_id
from app.core.permissions import require_permission
from app.modules.audit import services
from app.modules.audit.schemas import AuditLogResponse
from app.modules.common.schemas import Page, SuccessResponse
from app.modules.identity.models import User

router = APIRouter(prefix="/audit", tags=["Audit Logs"])


@router.get(
    "/logs",
    response_model=SuccessResponse[Page[AuditLogResponse]],
    dependencies=[Depends(require_permission("audit:view"))],
)
async def list_audit_logs(
    q: str | None = Query(None, max_length=128),
    action: str | None = Query(None, max_length=64),
    resource_type: str | None = Query(None, max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_audit_logs(
        db,
        require_school_id(current_user),
        q=q,
        action=action,
        resource_type=resource_type,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse[Page[AuditLogResponse]](
        data=Page[AuditLogResponse](items=items, meta=meta)
    )
