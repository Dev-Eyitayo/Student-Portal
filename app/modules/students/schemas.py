from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class Gender(StrEnum):
    M = "M"
    F = "F"
    MALE = "MALE"
    FEMALE = "FEMALE"


class StudentStatus(StrEnum):
    ADMITTED = "ADMITTED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    WITHDRAWN = "WITHDRAWN"
    TRANSFERRED = "TRANSFERRED"
    GRADUATED = "GRADUATED"


class EnrollmentStatus(StrEnum):
    ENROLLED = "ENROLLED"
    PROMOTED = "PROMOTED"
    REPEATING = "REPEATING"
    TRANSFERRED = "TRANSFERRED"
    WITHDRAWN = "WITHDRAWN"


class StudentCreateRequest(BaseModel):
    admission_number: str = Field(..., min_length=1, max_length=64)
    first_name: str = Field(..., min_length=1, max_length=64)
    middle_name: str | None = Field(None, max_length=64)
    last_name: str = Field(..., min_length=1, max_length=64)
    date_of_birth: date
    gender: Gender
    guardian_name: str = Field(..., min_length=2, max_length=128)
    guardian_email: EmailStr
    guardian_phone: str = Field(..., min_length=5, max_length=32)
    enrollment_date: date

    @model_validator(mode="after")
    def validate_dates(self) -> "StudentCreateRequest":
        if self.date_of_birth >= self.enrollment_date:
            raise ValueError("The date of birth must be before the enrollment date.")
        return self


class StudentUpdateRequest(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=64)
    middle_name: str | None = Field(None, max_length=64)
    last_name: str | None = Field(None, min_length=1, max_length=64)
    date_of_birth: date | None = None
    gender: Gender | None = None
    guardian_name: str | None = Field(None, min_length=2, max_length=128)
    guardian_email: EmailStr | None = None
    guardian_phone: str | None = Field(None, min_length=5, max_length=32)
    enrollment_date: date | None = None


class StudentStatusUpdateRequest(BaseModel):
    status: StudentStatus
    reason: str = Field(..., min_length=5, max_length=500)


class StudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    admission_number: str
    first_name: str
    middle_name: str | None = None
    last_name: str
    date_of_birth: date
    gender: Gender
    guardian_name: str
    guardian_email: EmailStr
    guardian_phone: str
    enrollment_date: date
    status: StudentStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    current_class_group_id: UUID | None = None
    current_class_name: str | None = None


class StudentDirectorySummaryResponse(BaseModel):
    total: int
    enrolled_current_session: int
    unenrolled: int
    withdrawn_or_graduated: int


class EnrollmentClassGroupSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    academic_session_id: UUID
    academic_level_id: UUID
    department_id: UUID | None = None
    class_arm_id: UUID


class EnrollmentSessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    start_date: date
    end_date: date


class EnrollmentCreateRequest(BaseModel):
    class_group_id: UUID


class EnrollmentUpdateRequest(BaseModel):
    enrollment_status: EnrollmentStatus
    promoted_to_class_group_id: UUID | None = None
    reason: str | None = Field(None, max_length=500)


class EnrollmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    student_id: UUID
    academic_session_id: UUID
    class_group_id: UUID
    enrollment_status: EnrollmentStatus
    promoted_to_class_group_id: UUID | None = None
    class_group: EnrollmentClassGroupSummary | None = None
    academic_session: EnrollmentSessionSummary | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    current_class_group_id: UUID | None = None
    current_class_name: str | None = None


class StudentBulkImportAcceptedResponse(BaseModel):
    task_id: str
    message: str


class StudentBulkImportStatusResponse(BaseModel):
    task_id: str
    state: str
    result: dict[str, Any] | None = None


class AttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"


class AttendanceSource(StrEnum):
    DAILY = "DAILY"
    MANUAL = "MANUAL"


class AttendanceRecordItem(BaseModel):
    student_enrollment_id: UUID
    status: AttendanceStatus


class ClassAttendanceMarkRequest(BaseModel):
    academic_term_id: UUID
    attendance_date: date
    reason: str | None = Field(None, max_length=500)
    records: list[AttendanceRecordItem] = Field(..., min_length=1)


class EnrollmentAttendanceMarkRequest(BaseModel):
    academic_term_id: UUID
    attendance_date: date
    status: AttendanceStatus
    reason: str | None = Field(None, max_length=500)


class AttendanceRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    student_enrollment_id: UUID
    academic_term_id: UUID
    attendance_date: date
    status: AttendanceStatus
    reason: str | None
    recorded_by_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ClassAttendanceMarkResponse(BaseModel):
    class_group_id: UUID
    academic_term_id: UUID
    attendance_date: date
    records_saved: int


class AttendanceSummaryUpdateRequest(BaseModel):
    academic_term_id: UUID
    times_school_opened: int = Field(..., ge=0, le=32767)
    times_present: int = Field(..., ge=0, le=32767)
    times_absent: int = Field(..., ge=0, le=32767)
    override_reason: str = Field(..., min_length=3, max_length=500)

    @field_validator("override_reason")
    @classmethod
    def strip_override_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("Please provide an override reason of at least 3 characters.")
        return cleaned

    @model_validator(mode="after")
    def validate_totals(self) -> "AttendanceSummaryUpdateRequest":
        if self.times_present + self.times_absent != self.times_school_opened:
            raise ValueError(
                "Days present plus days absent must equal the total days school opened."
            )
        return self


class ClassAttendanceSummaryItem(BaseModel):
    student_enrollment_id: UUID
    times_school_opened: int = Field(..., ge=0, le=32767)
    times_present: int = Field(..., ge=0, le=32767)
    times_absent: int = Field(..., ge=0, le=32767)

    @model_validator(mode="after")
    def validate_totals(self) -> "ClassAttendanceSummaryItem":
        if self.times_present + self.times_absent != self.times_school_opened:
            raise ValueError(
                "Days present plus days absent must equal the total days school opened."
            )
        return self


class ClassAttendanceSummaryUpdateRequest(BaseModel):
    academic_term_id: UUID
    override_reason: str = Field(..., min_length=3, max_length=500)
    summaries: list[ClassAttendanceSummaryItem] = Field(..., min_length=1)

    @field_validator("override_reason")
    @classmethod
    def strip_override_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("Please provide an override reason of at least 3 characters.")
        return cleaned


class AttendanceSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    student_enrollment_id: UUID
    academic_term_id: UUID
    times_school_opened: int
    times_present: int
    times_absent: int
    source: AttendanceSource
    override_reason: str | None
    updated_by_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ClassAttendanceRosterItem(BaseModel):
    student_enrollment_id: UUID
    student_id: UUID
    admission_number: str
    student_name: str
    status: AttendanceStatus | None = None
    backdate_reason: str | None = None


class ClassAttendanceRosterResponse(BaseModel):
    class_group_id: UUID
    academic_term_id: UUID
    attendance_date: date
    items: list[ClassAttendanceRosterItem]


class ClassAttendanceSummaryRow(BaseModel):
    student_enrollment_id: UUID
    student_id: UUID
    admission_number: str
    student_name: str
    times_school_opened: int
    times_present: int
    times_absent: int
    source: AttendanceSource | None = None
    override_reason: str | None = None


class AttendanceOverviewSummaryResponse(BaseModel):
    academic_term_id: UUID
    target_date: date
    days_school_opened: int
    present_today_pct: float
    classes_marked_today: int
    total_classes: int
    manual_overrides: int
