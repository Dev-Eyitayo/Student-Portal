from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID | None = None
    actor_name: str | None = None
    actor_email: str | None = None
    action: str
    resource_type: str
    resource_id: UUID
    resource_summary: str
    diff_payload: dict[str, Any]
    timestamp: datetime
