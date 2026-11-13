from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.modules.common.models import Base, TenantMixin, generate_uuid7

if TYPE_CHECKING:
    from app.modules.computation.models import TermReportCard


class ResultAccessToken(Base, TenantMixin):
    __tablename__ = "result_access_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    term_report_card_id: Mapped[UUID] = mapped_column(
        ForeignKey("term_report_cards.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_access_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_accessed_ip: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    __table_args__ = (
        Index("idx_result_tokens_lookup", "token_hash", "is_revoked", "expires_at"),
    )

    term_report_card: Mapped["TermReportCard"] = relationship("TermReportCard")
