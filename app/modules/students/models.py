from datetime import date
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.common.models import Base, TenantMixin, TimestampMixin, generate_uuid7

if TYPE_CHECKING:
    from app.modules.academic_structure.models import AcademicSession, AcademicTerm, ClassGroup
    from app.modules.identity.models import User
    from app.modules.subjects.models import StudentSubjectRegistration


class Student(Base, TenantMixin, TimestampMixin):
    __tablename__ = "students"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    admission_number: Mapped[str] = mapped_column(String(64), nullable=False)
    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_name: Mapped[str] = mapped_column(String(64), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(1), nullable=False)  # 'M' or 'F'
    guardian_name: Mapped[str] = mapped_column(String(128), nullable=False)
    guardian_email: Mapped[str] = mapped_column(String(255), nullable=False)
    guardian_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    enrollment_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="ADMITTED", nullable=False
    )  # ADMITTED, ACTIVE, TRANSFERRED, GRADUATED, WITHDRAWN, SUSPENDED

    __table_args__ = (
        UniqueConstraint("school_id", "admission_number", name="uq_school_admission_number"),
        CheckConstraint("gender IN ('M', 'F')", name="chk_student_gender"),
        CheckConstraint(
            "status IN ('ADMITTED', 'ACTIVE', 'SUSPENDED', 'WITHDRAWN', 'TRANSFERRED', 'GRADUATED')",
            name="chk_student_status",
        ),
        Index("idx_students_search", "school_id", "last_name", "first_name"),
    )

    enrollments: Mapped[list["StudentEnrollment"]] = relationship(
        "StudentEnrollment", back_populates="student", passive_deletes=True
    )


class StudentEnrollment(Base, TenantMixin, TimestampMixin):
    __tablename__ = "student_enrollments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    student_id: Mapped[UUID] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    academic_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    class_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_groups.id", ondelete="RESTRICT"), nullable=False
    )
    enrollment_status: Mapped[str] = mapped_column(
        String(32), default="ENROLLED", nullable=False
    )  # ENROLLED, PROMOTED, REPEATING, TRANSFERRED, WITHDRAWN
    promoted_to_class_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("class_groups.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("student_id", "academic_session_id", name="uq_student_session"),
        CheckConstraint(
            "enrollment_status IN ('ENROLLED', 'PROMOTED', 'REPEATING', 'TRANSFERRED', 'WITHDRAWN')",
            name="chk_enrollment_status",
        ),
        Index("idx_enrollments_class", "class_group_id", "enrollment_status"),
    )

    student: Mapped["Student"] = relationship("Student", back_populates="enrollments")
    academic_session: Mapped["AcademicSession"] = relationship("AcademicSession")
    class_group: Mapped["ClassGroup"] = relationship("ClassGroup", foreign_keys=[class_group_id])
    promoted_to_class_group: Mapped[Optional["ClassGroup"]] = relationship(
        "ClassGroup", foreign_keys=[promoted_to_class_group_id]
    )
    subject_registrations: Mapped[list["StudentSubjectRegistration"]] = relationship(
        "StudentSubjectRegistration",
        back_populates="student_enrollment",
        passive_deletes=True,
    )


class AttendanceRecord(Base, TenantMixin, TimestampMixin):
    __tablename__ = "attendance_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    student_enrollment_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    academic_term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_terms.id", ondelete="RESTRICT"), nullable=False
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "student_enrollment_id",
            "attendance_date",
            name="uq_attendance_enrollment_date",
        ),
        CheckConstraint(
            "status IN ('PRESENT', 'ABSENT', 'LATE')",
            name="chk_attendance_record_status",
        ),
        Index("idx_attendance_records_term", "school_id", "academic_term_id", "attendance_date"),
    )

    student_enrollment: Mapped["StudentEnrollment"] = relationship("StudentEnrollment")
    academic_term: Mapped["AcademicTerm"] = relationship("AcademicTerm")
    recorded_by: Mapped[Optional["User"]] = relationship("User")


class AttendanceSummary(Base, TenantMixin, TimestampMixin):
    __tablename__ = "attendance_summaries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    student_enrollment_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    academic_term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_terms.id", ondelete="RESTRICT"), nullable=False
    )
    times_school_opened: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    times_present: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    times_absent: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="DAILY", nullable=False)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "student_enrollment_id",
            "academic_term_id",
            name="uq_attendance_summary_enrollment_term",
        ),
        CheckConstraint(
            "source IN ('DAILY', 'MANUAL')",
            name="chk_attendance_summary_source",
        ),
        CheckConstraint(
            "times_school_opened >= 0 AND times_present >= 0 AND times_absent >= 0",
            name="chk_attendance_summary_non_negative",
        ),
        Index("idx_attendance_summaries_term", "school_id", "academic_term_id"),
    )

    student_enrollment: Mapped["StudentEnrollment"] = relationship("StudentEnrollment")
    academic_term: Mapped["AcademicTerm"] = relationship("AcademicTerm")
    updated_by: Mapped[Optional["User"]] = relationship("User")
