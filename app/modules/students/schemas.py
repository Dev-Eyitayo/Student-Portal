from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class StudentCreateRequest(BaseModel):
    admission_number: str = Field(..., min_length=1, max_length=64)
    first_name: str = Field(..., min_length=1, max_length=64)
    middle_name: str | None = Field(None, max_length=64)
    last_name: str = Field(..., min_length=1, max_length=64)
    date_of_birth: date
    gender: str = Field(..., min_length=1, max_length=10)
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
    gender: str | None = Field(None, min_length=1, max_length=10)
    guardian_name: str | None = Field(None, min_length=2, max_length=128)
    guardian_email: EmailStr | None = None
    guardian_phone: str | None = Field(None, min_length=5, max_length=32)
    enrollment_date: date | None = None


class StudentStatusUpdateRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=32)
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
    gender: str
    guardian_name: str
    guardian_email: EmailStr
    guardian_phone: str
    enrollment_date: date
    status: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


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
    enrollment_status: str = Field(..., min_length=1, max_length=32)
    promoted_to_class_group_id: UUID | None = None
    reason: str | None = Field(None, max_length=500)


class EnrollmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    student_id: UUID
    academic_session_id: UUID
    class_group_id: UUID
    enrollment_status: str
    promoted_to_class_group_id: UUID | None = None
    class_group: EnrollmentClassGroupSummary | None = None
    academic_session: EnrollmentSessionSummary | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class StudentBulkImportAcceptedResponse(BaseModel):
    task_id: str
    message: str


class StudentBulkImportStatusResponse(BaseModel):
    task_id: str
    state: str
    result: dict[str, Any] | None = None
