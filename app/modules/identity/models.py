from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.common.models import Base, TimestampMixin, generate_uuid7


class School(Base, TimestampMixin):
    __tablename__ = "schools"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    official_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), default="NG", nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    city: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    school_type: Mapped[str] = mapped_column(
        String(16), default="SECONDARY", server_default="SECONDARY", nullable=False
    )
    school_sector: Mapped[str] = mapped_column(
        String(16), default="PRIVATE", server_default="PRIVATE", nullable=False
    )
    logo_storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    stamp_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    principal_signature_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "school_type IN ('PRIMARY', 'SECONDARY', 'COMBINED')",
            name="chk_schools_school_type",
        ),
        CheckConstraint(
            "school_sector IN ('PUBLIC', 'PRIVATE')",
            name="chk_schools_school_sector",
        ),
    )

    # Relationships
    configuration: Mapped[Optional["SchoolConfiguration"]] = relationship(
        "SchoolConfiguration", back_populates="school", uselist=False, cascade="all, delete-orphan"
    )
    users: Mapped[list["User"]] = relationship("User", back_populates="school")


class SchoolConfiguration(Base, TimestampMixin):
    __tablename__ = "school_configurations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    grading_scale_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    assessment_weight_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    academic_term_definitions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    annual_results_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Lagos", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    report_card_template_id: Mapped[str] = mapped_column(
        String(64), default="default_v1", nullable=False
    )
    show_department_position_on_report_card: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    show_level_position_on_report_card: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    show_subject_position_on_report_card: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    setup_wizard_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    setup_wizard_dismissed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    school: Mapped["School"] = relationship("School", back_populates="configuration")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("schools.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    last_name: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # SUPER_ADMIN, SCHOOL_ADMIN, ACADEMIC_OFFICER, TEACHER
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_valid_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    school: Mapped[Optional["School"]] = relationship("School", back_populates="users")
    staff_profile: Mapped[Optional["StaffProfile"]] = relationship(
        "StaffProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    permission_grants: Mapped[list["UserPermissionGrant"]] = relationship(
        "UserPermissionGrant", foreign_keys="UserPermissionGrant.user_id", back_populates="user"
    )


class StaffProfile(Base, TimestampMixin):
    __tablename__ = "staff_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    staff_id_number: Mapped[str] = mapped_column(String(64), nullable=False)
    employment_status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    specialization: Mapped[str | None] = mapped_column(String(128), nullable=True)
    level_categories: Mapped[list[str]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default=list, nullable=False
    )
    signature_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("school_id", "staff_id_number", name="uq_staff_school_id_number"),
    )

    user: Mapped["User"] = relationship("User", back_populates="staff_profile")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # ASSESSMENT, STUDENT, STRUCTURE, ADMIN
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )


class RoleDefaultPermission(Base):
    __tablename__ = "role_default_permissions"

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    permission_id: Mapped[UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class SchoolRole(Base, TimestampMixin):
    __tablename__ = "school_roles"
    __table_args__ = (
        Index(
            "uq_school_roles_school_code_active",
            "school_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID] = mapped_column(
        ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SchoolRolePermission(Base):
    __tablename__ = "school_role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "school_id",
            "role",
            "permission_id",
            name="uq_school_role_permissions_school_role_perm",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID] = mapped_column(
        ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    permission_id: Mapped[UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )


class UserPermissionGrant(Base, TimestampMixin):
    __tablename__ = "user_permission_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )
    granted_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # Scoping
    scope_type: Mapped[str] = mapped_column(String(32), default="SCHOOL_WIDE", nullable=False)
    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), nullable=True
    )
    academic_level_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("academic_levels.id", ondelete="CASCADE"), nullable=True
    )
    class_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=True
    )

    __table_args__ = (
        Index(
            "uq_user_permission_grant_active",
            "user_id",
            "permission_id",
            "scope_type",
            "department_id",
            "academic_level_id",
            "class_group_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_user_permission_grants_lookup", "user_id", "school_id"),
    )

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], back_populates="permission_grants"
    )
    permission: Mapped["Permission"] = relationship("Permission")
