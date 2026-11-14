from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.common.models import Base, TenantMixin, TimestampMixin, generate_uuid7

if TYPE_CHECKING:
    from app.modules.academic_structure.models import AcademicTerm
    from app.modules.identity.models import User
    from app.modules.students.models import StudentEnrollment
    from app.modules.subjects.models import ClassSubject


class AssessmentDefinition(Base, TenantMixin):
    __tablename__ = "assessment_definitions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_terms.id", ondelete="CASCADE"), nullable=False
    )
    class_subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_subjects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # e.g. 'Test 1', 'Test 2', 'Assignments/Attendance', 'Terminal Examination'
    code: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # e.g. 'TEST1', 'TEST2', 'ASSIGN', 'EXAM'
    assessment_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # 'CONTINUOUS_ASSESSMENT', 'EXAMINATION', 'PRACTICAL'
    max_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    weight_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    sequence_order: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "academic_term_id",
            "class_subject_id",
            "code",
            name="uq_term_class_subject_assessment_code",
        ),
        CheckConstraint("max_score > 0", name="chk_assessment_max_score"),
        CheckConstraint(
            "weight_percentage >= 0 AND weight_percentage <= 100", name="chk_assessment_weight"
        ),
    )

    academic_term: Mapped["AcademicTerm"] = relationship("AcademicTerm")
    class_subject: Mapped["ClassSubject"] = relationship("ClassSubject")


class AssessmentScore(Base, TenantMixin, TimestampMixin):
    __tablename__ = "assessment_scores"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    assessment_definition_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_definitions.id", ondelete="CASCADE"), nullable=False
    )
    student_enrollment_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_enrollments.id", ondelete="CASCADE"), nullable=False
    )
    score_status: Mapped[str] = mapped_column(
        String(16), default="SCORED", nullable=False
    )  # 'SCORED', 'ABSENT', 'EXEMPT', 'PENDING'
    raw_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    recorded_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    last_modified_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "assessment_definition_id", "student_enrollment_id", name="uq_student_assessment_score"
        ),
        CheckConstraint(
            "(score_status = 'SCORED' AND raw_score IS NOT NULL AND raw_score >= 0) OR "
            "(score_status IN ('ABSENT', 'EXEMPT', 'PENDING') AND raw_score IS NULL)",
            name="chk_score_validity",
        ),
        Index("idx_scores_enrollment", "student_enrollment_id"),
    )

    assessment_definition: Mapped["AssessmentDefinition"] = relationship("AssessmentDefinition")
    student_enrollment: Mapped["StudentEnrollment"] = relationship("StudentEnrollment")
    recorded_by: Mapped["User"] = relationship("User", foreign_keys=[recorded_by_id])
    last_modified_by: Mapped["User"] = relationship("User", foreign_keys=[last_modified_by_id])
