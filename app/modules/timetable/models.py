from datetime import datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.common.models import Base, TenantMixin, TimestampMixin, generate_uuid7


class BellSchedule(Base, TenantMixin, TimestampMixin):
    __tablename__ = "bell_schedules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    level_category: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # PRIMARY | JUNIOR_SECONDARY | SENIOR_SECONDARY
    academic_level_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("academic_levels.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    slots: Mapped[list["TimetableSlot"]] = relationship(
        "TimetableSlot", back_populates="bell_schedule"
    )

    __table_args__ = (
        Index(
            "uq_bell_schedule_cat_active",
            "school_id",
            "academic_session_id",
            "level_category",
            unique=True,
            postgresql_where=text("academic_level_id IS NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_bell_schedule_level_active",
            "school_id",
            "academic_session_id",
            "academic_level_id",
            unique=True,
            postgresql_where=text("academic_level_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )


class TimetableSlot(Base, TenantMixin, TimestampMixin):
    __tablename__ = "timetable_slots"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    bell_schedule_id: Mapped[UUID] = mapped_column(
        ForeignKey("bell_schedules.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1..6
    period_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    slot_type: Mapped[str] = mapped_column(
        String(32), default="TEACHING", nullable=False
    )  # TEACHING | ASSEMBLY | SHORT_BREAK | LONG_BREAK | CLUBS_SPORTS | PREP

    bell_schedule: Mapped["BellSchedule"] = relationship("BellSchedule", back_populates="slots")

    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 1 AND 6", name="ck_timetable_slot_day"),
        CheckConstraint("period_order >= 1", name="ck_timetable_slot_order"),
        CheckConstraint("end_time > start_time", name="ck_timetable_slot_time_order"),
        Index(
            "uq_timetable_slot_order_active",
            "bell_schedule_id",
            "day_of_week",
            "period_order",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Room(Base, TenantMixin, TimestampMixin):
    __tablename__ = "rooms"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    room_type: Mapped[str] = mapped_column(
        String(32), default="SCIENCE_LAB", nullable=False
    )  # CLASSROOM | SCIENCE_LAB | ICT_LAB | WORKSHOP | HALL
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint("capacity IS NULL OR capacity > 0", name="ck_room_capacity_positive"),
        Index(
            "uq_room_code_active",
            "school_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class SubjectSchedulingRule(Base, TenantMixin, TimestampMixin):
    __tablename__ = "subject_scheduling_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    academic_level_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("academic_levels.id", ondelete="RESTRICT"), nullable=True
    )
    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True
    )
    subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    periods_per_week: Mapped[int] = mapped_column(SmallInteger, default=3, nullable=False)
    double_periods_per_week: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    allow_double_period_split: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_periods_per_day: Mapped[int] = mapped_column(SmallInteger, default=2, nullable=False)
    prefer_morning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    preferred_room_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=True
    )
    room_only_for_double_periods: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    __table_args__ = (
        CheckConstraint("periods_per_week >= 1", name="ck_rule_periods_positive"),
        CheckConstraint("double_periods_per_week >= 0", name="ck_rule_doubles_non_negative"),
        CheckConstraint(
            "double_periods_per_week * 2 <= periods_per_week",
            name="ck_rule_doubles_fit_weekly",
        ),
        CheckConstraint("max_periods_per_day >= 1", name="ck_rule_max_day_positive"),
        Index(
            "uq_rule_school_default",
            "school_id",
            "academic_session_id",
            "subject_id",
            unique=True,
            postgresql_where=text(
                "academic_level_id IS NULL AND department_id IS NULL AND deleted_at IS NULL"
            ),
        ),
        Index(
            "uq_rule_level_default",
            "school_id",
            "academic_session_id",
            "academic_level_id",
            "subject_id",
            unique=True,
            postgresql_where=text(
                "academic_level_id IS NOT NULL AND department_id IS NULL AND deleted_at IS NULL"
            ),
        ),
        Index(
            "uq_rule_level_dept",
            "school_id",
            "academic_session_id",
            "academic_level_id",
            "department_id",
            "subject_id",
            unique=True,
            postgresql_where=text(
                "academic_level_id IS NOT NULL AND department_id IS NOT NULL AND deleted_at IS NULL"
            ),
        ),
    )


class TimetableSyncGroup(Base, TenantMixin, TimestampMixin):
    __tablename__ = "timetable_sync_groups"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sync_type: Mapped[str] = mapped_column(
        String(24), nullable=False
    )  # JOINT_CLASS | PARALLEL_SPLIT

    items: Mapped[list["TimetableSyncGroupItem"]] = relationship(
        "TimetableSyncGroupItem", back_populates="sync_group"
    )


class TimetableSyncGroupItem(Base, TenantMixin, TimestampMixin):
    __tablename__ = "timetable_sync_group_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    sync_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("timetable_sync_groups.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    class_subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_subjects.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    sync_group: Mapped["TimetableSyncGroup"] = relationship(
        "TimetableSyncGroup", back_populates="items"
    )

    __table_args__ = (
        Index(
            "uq_sync_group_item_active",
            "sync_group_id",
            "class_subject_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class TeacherAvailability(Base, TenantMixin, TimestampMixin):
    __tablename__ = "teacher_availabilities"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    day_of_week: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    availability_type: Mapped[str] = mapped_column(
        String(16), default="UNAVAILABLE", nullable=False
    )  # UNAVAILABLE | AVOID | LOAD_CAP
    max_periods_per_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    max_periods_per_week: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    max_consecutive_periods: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    min_gap_minutes_between_categories: Mapped[int] = mapped_column(
        SmallInteger, default=0, nullable=False
    )


class Timetable(Base, TenantMixin, TimestampMixin):
    __tablename__ = "timetables"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    academic_term_id: Mapped[UUID] = mapped_column(
        ForeignKey("academic_terms.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    parent_timetable_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("timetables.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="DRAFT", nullable=False
    )  # DRAFT | PUBLISHED | ARCHIVED
    generation_status: Mapped[str] = mapped_column(
        String(24), default="IDLE", nullable=False
    )  # IDLE | QUEUED | RUNNING | COMPLETED | FAILED
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    entries: Mapped[list["TimetableEntry"]] = relationship(
        "TimetableEntry", back_populates="timetable"
    )

    __table_args__ = (
        Index(
            "uq_published_timetable_per_term",
            "school_id",
            "academic_term_id",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED' AND deleted_at IS NULL"),
        ),
    )


class TimetableEntry(Base, TenantMixin, TimestampMixin):
    __tablename__ = "timetable_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    timetable_id: Mapped[UUID] = mapped_column(
        ForeignKey("timetables.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    class_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_groups.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    class_subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("class_subjects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    teacher_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    timetable_slot_id: Mapped[UUID] = mapped_column(
        ForeignKey("timetable_slots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    room_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=True
    )
    sync_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("timetable_sync_groups.id", ondelete="RESTRICT"), nullable=True
    )
    is_double_period_part: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    double_period_pair_id: Mapped[UUID | None] = mapped_column(nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    timetable: Mapped["Timetable"] = relationship("Timetable", back_populates="entries")
    entry_teachers: Mapped[list["TimetableEntryTeacher"]] = relationship(
        "TimetableEntryTeacher", back_populates="entry"
    )

    __table_args__ = (
        Index(
            "uq_timetable_class_slot_active",
            "timetable_id",
            "class_group_id",
            "class_subject_id",
            "timetable_slot_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class TimetableEntryTeacher(Base, TenantMixin, TimestampMixin):
    __tablename__ = "timetable_entry_teachers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    timetable_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("timetable_entries.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    teacher_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    entry: Mapped["TimetableEntry"] = relationship(
        "TimetableEntry", back_populates="entry_teachers"
    )

    __table_args__ = (
        Index(
            "uq_entry_teacher_active",
            "timetable_entry_id",
            "teacher_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
