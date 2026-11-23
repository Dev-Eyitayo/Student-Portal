from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.common.models import Base, TenantMixin, TimestampMixin, generate_uuid7

if TYPE_CHECKING:
    from app.modules.academic_structure.models import AcademicTerm
    from app.modules.identity.models import User
    from app.modules.students.models import StudentEnrollment
    from app.modules.subjects.models import ClassSubject


class SubjectResult(Base, TenantMixin, TimestampMixin):
    __tablename__ = "subject_results"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_terms.id", ondelete="CASCADE"), nullable=False
    )
    class_subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_subjects.id", ondelete="CASCADE"), nullable=False
    )
    student_enrollment_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_enrollments.id", ondelete="CASCADE"), nullable=False
    )
    total_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)  # 'A1', 'B2', 'B3', etc.
    grade_point: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    remark: Mapped[str] = mapped_column(String(64), nullable=False)  # 'EXCELLENT', 'CREDIT'
    subject_position: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    class_highest_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    class_lowest_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    class_average_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    # Annual Cumulative Fields (populated on Third Term reports)
    first_term_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    second_term_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    third_term_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    cumulative_annual_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    cumulative_annual_grade: Mapped[str | None] = mapped_column(String(8), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), default="DRAFT", nullable=False
    )  # 'DRAFT', 'SUBMITTED', 'APPROVED', 'PUBLISHED'
    approved_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen_config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "academic_term_id",
            "student_enrollment_id",
            "class_subject_id",
            name="uq_term_student_class_subject",
        ),
    )

    academic_term: Mapped["AcademicTerm"] = relationship("AcademicTerm")
    class_subject: Mapped["ClassSubject"] = relationship("ClassSubject")
    student_enrollment: Mapped["StudentEnrollment"] = relationship("StudentEnrollment")
    approved_by: Mapped[Optional["User"]] = relationship("User")


class TermReportCard(Base, TenantMixin, TimestampMixin):
    __tablename__ = "term_report_cards"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_terms.id", ondelete="CASCADE"), nullable=False
    )
    student_enrollment_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_enrollments.id", ondelete="CASCADE"), nullable=False
    )
    total_obtainable_marks: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    total_marks_obtained: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    overall_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    class_position: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    total_students_in_arm: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    level_position: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    total_students_in_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    times_school_opened: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    times_present: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    times_absent: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    teacher_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    principal_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Annual Cumulative & Promotion Fields
    is_cumulative_annual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cumulative_total_obtainable: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 2), nullable=True
    )
    cumulative_total_obtained: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    cumulative_average_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    cumulative_class_position: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    cumulative_level_position: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    promotion_decision: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # 'PROMOTED', 'REPEATING', 'PROMOTED_ON_TRIAL', 'WITHDRAWN'

    publication_status: Mapped[str] = mapped_column(
        String(32), default="DRAFT", nullable=False
    )  # 'DRAFT', 'APPROVED', 'PUBLISHED'
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen_config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "academic_term_id", "student_enrollment_id", name="uq_term_student_report_card"
        ),
        Index("idx_report_cards_lookup", "school_id", "academic_term_id", "publication_status"),
    )

    academic_term: Mapped["AcademicTerm"] = relationship("AcademicTerm")
    student_enrollment: Mapped["StudentEnrollment"] = relationship("StudentEnrollment")
