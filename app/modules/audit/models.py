from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.common.models import Base, generate_uuid7

if TYPE_CHECKING:
    from app.modules.identity.models import School, User


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), nullable=True
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_ip: Mapped[str] = mapped_column(INET, nullable=False)
    action: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # 'SCORE_ENTRY', 'SCORE_MODIFY', etc.
    resource_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # 'AssessmentScore', 'SubjectResult'
    resource_id: Mapped[UUID] = mapped_column(nullable=False)
    diff_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    __table_args__ = (
        Index("idx_audit_logs_tenant_resource", "school_id", "resource_type", "resource_id"),
        Index("idx_audit_logs_actor", "actor_id", "timestamp"),
    )

    school: Mapped[Optional["School"]] = relationship("School")
    actor: Mapped[Optional["User"]] = relationship("User")
