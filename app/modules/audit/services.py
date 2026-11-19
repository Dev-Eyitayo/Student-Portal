from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.audit.schemas import AuditLogResponse
from app.modules.common.schemas import PageMeta
from app.modules.identity.models import User


@dataclass(frozen=True)
class AuditContext:
    school_id: UUID
    actor_id: UUID
    actor_ip: str


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (UUID, date, datetime)):
        return str(value)
    return value


def record_audit_log(
    session: AsyncSession,
    context: AuditContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    diff_payload: dict[str, Any],
) -> AuditLog:
    log = AuditLog(
        school_id=context.school_id,
        actor_id=context.actor_id,
        actor_ip=context.actor_ip,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        diff_payload=json_safe(diff_payload),
    )
    session.add(log)
    return log


async def list_audit_logs(
    db: AsyncSession,
    school_id: UUID,
    *,
    action: str | None = None,
    resource_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AuditLogResponse], PageMeta]:
    stmt = (
        select(AuditLog, User.first_name, User.last_name, User.email)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(AuditLog.school_id == school_id)
    )
    count_stmt = select(func.count()).select_from(AuditLog).where(AuditLog.school_id == school_id)

    if action:
        stmt = stmt.where(AuditLog.action == action.strip())
        count_stmt = count_stmt.where(AuditLog.action == action.strip())
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type.strip())
        count_stmt = count_stmt.where(AuditLog.resource_type == resource_type.strip())

    total = (await db.execute(count_stmt)).scalar_one()
    offset = (page - 1) * page_size
    rows = (
        await db.execute(stmt.order_by(AuditLog.timestamp.desc()).offset(offset).limit(page_size))
    ).all()

    items = [
        AuditLogResponse(
            id=log.id,
            actor_id=log.actor_id,
            actor_name=f"{first_name} {last_name}".strip() if first_name or last_name else None,
            actor_email=email,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            diff_payload=log.diff_payload,
            timestamp=log.timestamp,
        )
        for log, first_name, last_name, email in rows
    ]
    return items, PageMeta.build(page=page, page_size=page_size, total=total)
