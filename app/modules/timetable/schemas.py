from datetime import datetime, time
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LevelCategory(StrEnum):
    PRIMARY = "PRIMARY"
    JUNIOR_SECONDARY = "JUNIOR_SECONDARY"
    SENIOR_SECONDARY = "SENIOR_SECONDARY"


class SlotType(StrEnum):
    TEACHING = "TEACHING"
    ASSEMBLY = "ASSEMBLY"
    SHORT_BREAK = "SHORT_BREAK"
    LONG_BREAK = "LONG_BREAK"
    CLUBS_SPORTS = "CLUBS_SPORTS"
    PREP = "PREP"


class RoomType(StrEnum):
    CLASSROOM = "CLASSROOM"
    SCIENCE_LAB = "SCIENCE_LAB"
    ICT_LAB = "ICT_LAB"
    WORKSHOP = "WORKSHOP"
    HALL = "HALL"


class SyncType(StrEnum):
    JOINT_CLASS = "JOINT_CLASS"
    PARALLEL_SPLIT = "PARALLEL_SPLIT"


class AvailabilityType(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    AVOID = "AVOID"
    LOAD_CAP = "LOAD_CAP"


class TimetableSlotCreate(BaseModel):
    day_of_week: int = Field(..., ge=1, le=6)
    period_order: int = Field(..., ge=1, le=20)
    label: str = Field(..., min_length=1, max_length=64)
    start_time: time
    end_time: time
    slot_type: SlotType = SlotType.TEACHING

    @model_validator(mode="after")
    def validate_times(self) -> "TimetableSlotCreate":
        if self.end_time <= self.start_time:
            raise ValueError("Slot end time must be after start time.")
        return self


class TimetableSlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    bell_schedule_id: UUID
    day_of_week: int
    period_order: int
    label: str
    start_time: time
    end_time: time
    slot_type: str


class BellScheduleCreateRequest(BaseModel):
    academic_session_id: UUID
    level_category: LevelCategory
    academic_level_id: UUID | None = None
    name: str = Field(..., min_length=2, max_length=100)
    slots: list[TimetableSlotCreate] = Field(default_factory=list)


class BellScheduleUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    is_active: bool | None = None


class BellScheduleSlotsReplaceRequest(BaseModel):
    slots: list[TimetableSlotCreate] = Field(..., min_length=1)


class BellScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_session_id: UUID
    level_category: str
    academic_level_id: UUID | None
    name: str
    is_active: bool
    slots: list[TimetableSlotResponse] = Field(default_factory=list)


class RoomCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    code: str = Field(..., min_length=1, max_length=24)
    room_type: RoomType = RoomType.SCIENCE_LAB
    capacity: int | None = Field(None, ge=1, le=2000)


class RoomUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    code: str | None = Field(None, min_length=1, max_length=24)
    room_type: RoomType | None = None
    capacity: int | None = Field(None, ge=1, le=2000)


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    code: str
    room_type: str
    capacity: int | None


class SubjectSchedulingRuleUpsertRequest(BaseModel):
    academic_session_id: UUID
    academic_level_id: UUID | None = None
    department_id: UUID | None = None
    subject_id: UUID
    periods_per_week: int = Field(..., ge=1, le=20)
    double_periods_per_week: int = Field(0, ge=0, le=10)
    allow_double_period_split: bool = False
    max_periods_per_day: int = Field(2, ge=1, le=6)
    prefer_morning: bool = False
    preferred_room_id: UUID | None = None
    room_only_for_double_periods: bool = True

    @model_validator(mode="after")
    def validate_rule(self) -> "SubjectSchedulingRuleUpsertRequest":
        if self.double_periods_per_week * 2 > self.periods_per_week:
            raise ValueError(
                "Double periods per week cannot exceed half of total periods per week."
            )
        if self.double_periods_per_week > 0 and self.max_periods_per_day < 2:
            raise ValueError(
                "Max periods per day must be at least 2 when double periods are enabled."
            )
        return self


class SubjectSchedulingRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_session_id: UUID
    academic_level_id: UUID | None
    department_id: UUID | None
    subject_id: UUID
    periods_per_week: int
    double_periods_per_week: int
    allow_double_period_split: bool
    max_periods_per_day: int
    prefer_morning: bool
    preferred_room_id: UUID | None
    room_only_for_double_periods: bool


class SyncGroupCreateRequest(BaseModel):
    academic_session_id: UUID
    name: str = Field(..., min_length=2, max_length=100)
    sync_type: SyncType
    class_subject_ids: list[UUID] = Field(..., min_length=2)


class SyncGroupUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    class_subject_ids: list[UUID] | None = Field(None, min_length=2)


class SyncGroupResponse(BaseModel):
    id: UUID
    school_id: UUID
    academic_session_id: UUID
    name: str
    sync_type: str
    class_subject_ids: list[UUID]


class TeacherAvailabilityUpsertRequest(BaseModel):
    academic_session_id: UUID
    teacher_id: UUID
    day_of_week: int | None = Field(None, ge=1, le=6)
    start_time: time | None = None
    end_time: time | None = None
    availability_type: AvailabilityType = AvailabilityType.UNAVAILABLE
    max_periods_per_day: int | None = Field(None, ge=1, le=12)
    max_periods_per_week: int | None = Field(None, ge=1, le=60)
    max_consecutive_periods: int | None = Field(None, ge=1, le=10)
    min_gap_minutes_between_categories: int = Field(0, ge=0, le=60)


class TeacherAvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_session_id: UUID
    teacher_id: UUID
    day_of_week: int | None
    start_time: time | None
    end_time: time | None
    availability_type: str
    max_periods_per_day: int | None
    max_periods_per_week: int | None
    max_consecutive_periods: int | None
    min_gap_minutes_between_categories: int


class TimetableCreateRequest(BaseModel):
    academic_term_id: UUID
    name: str = Field(..., min_length=2, max_length=120)


class TimetableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_term_id: UUID
    parent_timetable_id: UUID | None
    version: int
    name: str
    status: str
    generation_status: str
    celery_task_id: str | None
    generation_summary: dict[str, Any] | None
    published_at: datetime | None
    published_by_id: UUID | None


class TimetableGenerateResponse(BaseModel):
    timetable_id: UUID
    task_id: str
    generation_status: str


class TimetableJobStatusResponse(BaseModel):
    timetable_id: UUID
    task_id: str
    state: str
    generation_status: str
    generation_summary: dict[str, Any] | None = None


class TimetableEntryMoveValidateRequest(BaseModel):
    target_slot_id: UUID
    target_room_id: UUID | None = None


class TimetableEntryMoveValidateResponse(BaseModel):
    valid: bool
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class TimetableEntryUpdateRequest(BaseModel):
    timetable_slot_id: UUID | None = None
    room_id: UUID | None = None
    is_pinned: bool | None = None


class TimetableEntryView(BaseModel):
    id: UUID
    class_group_id: UUID
    class_group_name: str
    level_category: str
    class_subject_id: UUID
    subject_id: UUID
    subject_code: str
    subject_name: str
    teacher_id: UUID | None
    teacher_name: str | None
    teacher_names: list[str] = Field(default_factory=list)
    timetable_slot_id: UUID
    day_of_week: int
    period_order: int
    slot_label: str
    start_time: time
    end_time: time
    room_id: UUID | None
    room_name: str | None
    sync_group_id: UUID | None
    is_double_period_part: bool
    double_period_pair_id: UUID | None
    is_pinned: bool


class TimetableGridResponse(BaseModel):
    timetable: TimetableResponse
    slots: list[TimetableSlotResponse]
    entries: list[TimetableEntryView]


class TimetableMasterGridResponse(BaseModel):
    timetable: TimetableResponse
    class_groups: list[dict[str, Any]]
    slots: list[TimetableSlotResponse]
    entries: list[TimetableEntryView]


class AssistantParseRequest(BaseModel):
    academic_session_id: UUID
    prompt: str = Field(..., min_length=3, max_length=500)


class AssistantActionPreview(BaseModel):
    action_type: (
        str  # CREATE_TEACHER_AVAILABILITY | UPSERT_SUBJECT_RULE | CREATE_SYNC_GROUP | UNSUPPORTED
    )
    summary: str
    confidence: str
    payload: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class RelaxationSuggestion(BaseModel):
    id: str
    diagnostic_index: int
    diagnostic_code: str
    diagnostic_message: str
    auto_fixable: bool
    title: str
    description: str
    action_type: str | None = None
    action_payload: dict[str, Any] = Field(default_factory=dict)


class ApplyRelaxationRequest(BaseModel):
    action_type: str
    action_payload: dict[str, Any] = Field(default_factory=dict)
