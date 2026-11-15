from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.common.models import Base, TenantMixin, TimestampMixin, generate_uuid7


class AcademicSession(Base, TenantMixin, TimestampMixin):
    __tablename__ = "academic_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    name: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g., '2024/2025'
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index(
            "uq_school_session_name_active",
            "school_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_current_session_per_school",
            "school_id",
            unique=True,
            postgresql_where=text("is_current AND deleted_at IS NULL"),
        ),
        CheckConstraint("end_date > start_date", name="chk_session_dates"),
    )

    terms: Mapped[list["AcademicTerm"]] = relationship(
        "AcademicTerm", back_populates="academic_session", passive_deletes=True
    )
    class_groups: Mapped[list["ClassGroup"]] = relationship(
        "ClassGroup", back_populates="academic_session", passive_deletes=True
    )


class AcademicTerm(Base, TenantMixin, TimestampMixin):
    __tablename__ = "academic_terms"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # 'First Term', 'Second Term', 'Third Term'
    term_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1, 2, 3
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_locked_for_scoring: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index(
            "uq_session_term_order_active",
            "academic_session_id",
            "term_order",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_session_term_name_active",
            "academic_session_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_current_term_per_school",
            "school_id",
            unique=True,
            postgresql_where=text("is_current AND deleted_at IS NULL"),
        ),
        CheckConstraint("term_order > 0", name="chk_term_order_positive"),
        CheckConstraint("end_date > start_date", name="chk_term_dates"),
    )

    academic_session: Mapped["AcademicSession"] = relationship(
        "AcademicSession", back_populates="terms"
    )


class AcademicLevel(Base, TenantMixin, TimestampMixin):
    __tablename__ = "academic_levels"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # 'JSS 1', 'SS 1'
    code: Mapped[str] = mapped_column(String(16), nullable=False)  # 'JSS1', 'SS1'
    level_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1, 2, 3, 4, 5, 6
    category: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # 'JUNIOR_SECONDARY', 'SENIOR_SECONDARY'

    __table_args__ = (
        Index(
            "uq_school_level_code_active",
            "school_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_school_level_order_active",
            "school_id",
            "level_order",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "category IN ('JUNIOR_SECONDARY', 'SENIOR_SECONDARY')",
            name="chk_level_category",
        ),
    )


class Department(Base, TenantMixin, TimestampMixin):
    __tablename__ = "departments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # 'Science', 'Arts', 'Commercial'
    code: Mapped[str] = mapped_column(String(16), nullable=False)  # 'SCI', 'ART', 'COMM'

    __table_args__ = (
        Index(
            "uq_school_department_code_active",
            "school_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class ClassArm(Base, TenantMixin, TimestampMixin):
    __tablename__ = "class_arms"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    name: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # e.g., 'O', 'L', 'I', 'V', 'A', 'B'

    __table_args__ = (
        Index(
            "uq_school_arm_name_active",
            "school_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class ClassGroup(Base, TenantMixin, TimestampMixin):
    """
    Concrete cohort instance in an academic session.
    Example: 'SS1 Science O' in 2024/2025 session.
    """

    __tablename__ = "class_groups"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    academic_level_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_levels.id", ondelete="RESTRICT"), nullable=False
    )
    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True
    )
    class_arm_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_arms.id", ondelete="RESTRICT"), nullable=False
    )
    class_teacher_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        Index(
            "uq_class_group_composition_with_dept",
            "school_id",
            "academic_session_id",
            "academic_level_id",
            "department_id",
            "class_arm_id",
            unique=True,
            postgresql_where=text("department_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_class_group_composition_no_dept",
            "school_id",
            "academic_session_id",
            "academic_level_id",
            "class_arm_id",
            unique=True,
            postgresql_where=text("department_id IS NULL AND deleted_at IS NULL"),
        ),
        Index("idx_class_groups_lookup", "school_id", "academic_session_id", "academic_level_id"),
    )

    academic_session: Mapped["AcademicSession"] = relationship(
        "AcademicSession", back_populates="class_groups"
    )
    academic_level: Mapped["AcademicLevel"] = relationship("AcademicLevel")
    department: Mapped[Optional["Department"]] = relationship("Department")
    class_arm: Mapped["ClassArm"] = relationship("ClassArm")
