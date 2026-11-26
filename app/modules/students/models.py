from datetime import date
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
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
    home_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
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
        Index(
            "idx_students_trgm_admission_number",
            "admission_number",
            postgresql_using="gin",
            postgresql_ops={"admission_number": "gin_trgm_ops"},
        ),
        Index(
            "idx_students_trgm_first_name",
            "first_name",
            postgresql_using="gin",
            postgresql_ops={"first_name": "gin_trgm_ops"},
        ),
        Index(
            "idx_students_trgm_middle_name",
            "middle_name",
            postgresql_using="gin",
            postgresql_ops={"middle_name": "gin_trgm_ops"},
        ),
        Index(
            "idx_students_trgm_last_name",
            "last_name",
            postgresql_using="gin",
            postgresql_ops={"last_name": "gin_trgm_ops"},
        ),
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


class Guardian(Base, TenantMixin, TimestampMixin):
    __tablename__ = "guardians"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    alternate_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(128), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index(
            "uq_guardian_school_phone_active",
            "school_id",
            "phone",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_guardians_school_email", "school_id", "email"),
        Index("idx_guardians_search", "school_id", "full_name"),
        Index(
            "idx_guardians_trgm_full_name",
            "full_name",
            postgresql_using="gin",
            postgresql_ops={"full_name": "gin_trgm_ops"},
        ),
        Index(
            "idx_guardians_trgm_email",
            "email",
            postgresql_using="gin",
            postgresql_ops={"email": "gin_trgm_ops"},
        ),
        Index(
            "idx_guardians_trgm_phone",
            "phone",
            postgresql_using="gin",
            postgresql_ops={"phone": "gin_trgm_ops"},
        ),
    )

    student_links: Mapped[list["StudentGuardian"]] = relationship(
        "StudentGuardian", back_populates="guardian", passive_deletes=True
    )


class StudentGuardian(Base, TenantMixin, TimestampMixin):
    __tablename__ = "student_guardians"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    student_id: Mapped[UUID] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    guardian_id: Mapped[UUID] = mapped_column(
        ForeignKey("guardians.id", ondelete="RESTRICT"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        String(32), default="GUARDIAN", nullable=False
    )  # FATHER, MOTHER, GUARDIAN, SPONSOR, OTHER
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("student_id", "guardian_id", name="uq_student_guardian"),
        CheckConstraint(
            "relationship_type IN ('FATHER', 'MOTHER', 'GUARDIAN', 'SPONSOR', 'OTHER')",
            name="chk_student_guardian_relationship",
        ),
        Index("idx_student_guardians_guardian", "guardian_id"),
    )

    student: Mapped["Student"] = relationship("Student")
    guardian: Mapped["Guardian"] = relationship("Guardian", back_populates="student_links")


class StudentMedicalProfile(Base, TenantMixin, TimestampMixin):
    __tablename__ = "student_medical_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    student_id: Mapped[UUID] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    blood_group: Mapped[str | None] = mapped_column(String(8), nullable=True)
    genotype: Mapped[str | None] = mapped_column(String(8), nullable=True)
    allergies: Mapped[str | None] = mapped_column(Text, nullable=True)
    chronic_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    medications: Mapped[str | None] = mapped_column(Text, nullable=True)
    dietary_restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    emergency_contact_relationship: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    student: Mapped["Student"] = relationship("Student")
    updated_by: Mapped[Optional["User"]] = relationship("User")
