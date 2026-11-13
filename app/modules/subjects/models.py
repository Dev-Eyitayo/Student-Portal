from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.modules.common.models import Base, TenantMixin, TimestampMixin, generate_uuid7

if TYPE_CHECKING:
    from app.modules.academic_structure.models import (
        AcademicLevel,
        AcademicSession,
        ClassGroup,
        Department,
    )
    from app.modules.identity.models import User
    from app.modules.students.models import StudentEnrollment


class Subject(Base, TenantMixin):
    __tablename__ = "subjects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    name: Mapped[str] = mapped_column(String(128), nullable=False)  # e.g., 'General Mathematics'
    code: Mapped[str] = mapped_column(String(16), nullable=False)  # e.g., 'MTH', 'CCP'
    is_elective: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_trade_subject: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("school_id", "code", name="uq_school_subject_code"),
    )

    offerings: Mapped[List["SubjectOffering"]] = relationship(
        "SubjectOffering", back_populates="subject", cascade="all, delete-orphan"
    )


class SubjectOffering(Base, TenantMixin):
    __tablename__ = "subject_offerings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False
    )
    academic_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_sessions.id", ondelete="CASCADE"), nullable=False
    )
    academic_level_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_levels.id", ondelete="RESTRICT"), nullable=False
    )
    department_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "subject_id", "academic_session_id", "academic_level_id", "department_id",
            name="uq_offering_unique"
        ),
    )

    subject: Mapped["Subject"] = relationship("Subject", back_populates="offerings")
    academic_session: Mapped["AcademicSession"] = relationship("AcademicSession")
    academic_level: Mapped["AcademicLevel"] = relationship("AcademicLevel")
    department: Mapped[Optional["Department"]] = relationship("Department")
    class_subjects: Mapped[List["ClassSubject"]] = relationship(
        "ClassSubject", back_populates="subject_offering", cascade="all, delete-orphan"
    )


class ClassSubject(Base, TenantMixin):
    __tablename__ = "class_subjects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    subject_offering_id: Mapped[UUID] = mapped_column(
        ForeignKey("subject_offerings.id", ondelete="RESTRICT"), nullable=False
    )
    class_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("subject_offering_id", "class_group_id", name="uq_class_subject"),
    )

    subject_offering: Mapped["SubjectOffering"] = relationship("SubjectOffering", back_populates="class_subjects")
    class_group: Mapped["ClassGroup"] = relationship("ClassGroup")
    teacher_assignments: Mapped[List["TeacherAssignment"]] = relationship(
        "TeacherAssignment", back_populates="class_subject", cascade="all, delete-orphan"
    )
    student_registrations: Mapped[List["StudentSubjectRegistration"]] = relationship(
        "StudentSubjectRegistration", back_populates="class_subject", cascade="all, delete-orphan"
    )


class StudentSubjectRegistration(Base, TenantMixin):
    __tablename__ = "student_subject_registrations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    student_enrollment_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_enrollments.id", ondelete="CASCADE"), nullable=False
    )
    class_subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_subjects.id", ondelete="CASCADE"), nullable=False
    )
    subject_category: Mapped[str] = mapped_column(
        String(32), default="COMPULSORY_CORE", nullable=False
    )  # 'COMPULSORY_CORE', 'DEPARTMENT_CORE', 'TRADE_ELECTIVE', 'FREE_ELECTIVE'
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("student_enrollment_id", "class_subject_id", name="uq_student_subject_registration"),
        Index("idx_student_subject_reg", "class_subject_id", "is_active"),
        Index("idx_student_subject_enrollment", "student_enrollment_id", "is_active"),
    )

    student_enrollment: Mapped["StudentEnrollment"] = relationship("StudentEnrollment", back_populates="subject_registrations")
    class_subject: Mapped["ClassSubject"] = relationship("ClassSubject", back_populates="student_registrations")


class TeacherAssignment(Base, TenantMixin, TimestampMixin):
    __tablename__ = "teacher_assignments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    class_subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_subjects.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, server_default=text("CURRENT_DATE"), nullable=False)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    __table_args__ = (
        UniqueConstraint("class_subject_id", "teacher_id", "is_active", name="uq_active_teacher_class_subject"),
        Index("idx_teacher_assignments_lookup", "teacher_id", "is_active"),
    )

    teacher: Mapped["User"] = relationship("User", foreign_keys=[teacher_id])
    assigned_by: Mapped["User"] = relationship("User", foreign_keys=[assigned_by_id])
    class_subject: Mapped["ClassSubject"] = relationship("ClassSubject", back_populates="teacher_assignments")
