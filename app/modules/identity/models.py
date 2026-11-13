from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
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
    logo_storage_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    stamp_image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    principal_signature_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    configuration: Mapped[Optional["SchoolConfiguration"]] = relationship(
        "SchoolConfiguration", back_populates="school", uselist=False, cascade="all, delete-orphan"
    )
    users: Mapped[List["User"]] = relationship("User", back_populates="school")


class SchoolConfiguration(Base, TimestampMixin):
    __tablename__ = "school_configurations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    grading_scale_schema: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    assessment_weight_schema: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    academic_term_definitions: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Lagos", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    report_card_template_id: Mapped[str] = mapped_column(String(64), default="default_v1", nullable=False)

    school: Mapped["School"] = relationship("School", back_populates="configuration")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("schools.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    last_name: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # SUPER_ADMIN, SCHOOL_ADMIN, ACADEMIC_OFFICER, TEACHER
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    token_valid_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    school: Mapped[Optional["School"]] = relationship("School", back_populates="users")
    staff_profile: Mapped[Optional["StaffProfile"]] = relationship(
        "StaffProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    permission_grants: Mapped[List["UserPermissionGrant"]] = relationship(
        "UserPermissionGrant", foreign_keys="UserPermissionGrant.user_id", back_populates="user"
    )


class StaffProfile(Base, TimestampMixin):
    __tablename__ = "staff_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    staff_id_number: Mapped[str] = mapped_column(String(64), nullable=False)
    employment_status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    specialization: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    signature_image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("school_id", "staff_id_number", name="uq_staff_school_id_number"),
    )

    user: Mapped["User"] = relationship("User", back_populates="staff_profile")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # ASSESSMENT, STUDENT, STRUCTURE, ADMIN
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )


class RoleDefaultPermission(Base):
    __tablename__ = "role_default_permissions"

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    permission_id: Mapped[UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class UserPermissionGrant(Base):
    __tablename__ = "user_permission_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    school_id: Mapped[UUID] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    permission_id: Mapped[UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)
    granted_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    # Scoping
    scope_type: Mapped[str] = mapped_column(String(32), default="SCHOOL_WIDE", nullable=False)
    department_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), nullable=True
    )
    academic_level_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("academic_levels.id", ondelete="CASCADE"), nullable=True
    )
    class_group_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "permission_id", "scope_type", "department_id", "academic_level_id", "class_group_id",
            name="uq_user_permission_grant"
        ),
        Index("idx_user_permission_grants_lookup", "user_id", "school_id"),
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id], back_populates="permission_grants")
    permission: Mapped["Permission"] = relationship("Permission")
