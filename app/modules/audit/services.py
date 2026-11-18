from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog


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
