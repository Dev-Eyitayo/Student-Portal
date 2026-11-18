from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LevelCategory(StrEnum):
    JUNIOR_SECONDARY = "JUNIOR_SECONDARY"
    SENIOR_SECONDARY = "SENIOR_SECONDARY"


class SessionCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=32)
    start_date: date
    end_date: date
    is_current: bool = False

    @model_validator(mode="after")
    def validate_dates(self) -> "SessionCreateRequest":
        if self.end_date <= self.start_date:
            raise ValueError("The session end date must be after its start date.")
        return self


class SessionUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=32)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    start_date: date
    end_date: date
    is_current: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class TermCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=32)
    term_order: int = Field(..., ge=1, le=20)
    start_date: date
    end_date: date
    is_current: bool = False
    is_locked_for_scoring: bool = False

    @model_validator(mode="after")
    def validate_dates(self) -> "TermCreateRequest":
        if self.end_date <= self.start_date:
            raise ValueError("The term end date must be after its start date.")
        return self


class TermUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=32)
    term_order: int | None = Field(None, ge=1, le=20)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None
    is_locked_for_scoring: bool | None = None


class TermResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_session_id: UUID
    name: str
    term_order: int
    start_date: date
    end_date: date
    is_current: bool
    is_locked_for_scoring: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class LevelCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    code: str = Field(..., min_length=1, max_length=16)
    level_order: int = Field(..., ge=1, le=20)
    category: LevelCategory


class LevelUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=64)
    code: str | None = Field(None, min_length=1, max_length=16)
    level_order: int | None = Field(None, ge=1, le=20)
    category: LevelCategory | None = None


class LevelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    code: str
    level_order: int
    category: LevelCategory
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class DepartmentCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    code: str = Field(..., min_length=1, max_length=16)


class DepartmentUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=64)
    code: str | None = Field(None, min_length=1, max_length=16)


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    code: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class ArmCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)


class ArmUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=32)


class ArmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class ClassGroupCreateRequest(BaseModel):
    academic_session_id: UUID
    academic_level_id: UUID
    department_id: UUID | None = None
    class_arm_id: UUID
    class_teacher_id: UUID | None = None
    display_name: str | None = Field(None, min_length=1, max_length=128)


class ClassGroupUpdateRequest(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=128)
    class_teacher_id: UUID | None = None


class StructureTemplateApplyRequest(BaseModel):
    template_id: str = Field(..., min_length=2, max_length=64)
    academic_session_id: UUID | None = None
    session_name: str | None = Field(None, min_length=2, max_length=32)
    session_start_date: date | None = None
    session_end_date: date | None = None
    include_class_groups: bool = True

    @model_validator(mode="after")
    def validate_session_details(self) -> "StructureTemplateApplyRequest":
        if self.academic_session_id is None:
            if not self.session_name:
                raise ValueError("Provide a session name when no existing session is selected.")
            if self.session_start_date is None or self.session_end_date is None:
                raise ValueError("Provide session start and end dates when creating a session.")
            if self.session_end_date <= self.session_start_date:
                raise ValueError("The session end date must be after its start date.")
        return self


class StructureTemplateApplyResponse(BaseModel):
    template_id: str
    academic_session_id: UUID
    levels_created: int
    levels_reused: int
    departments_created: int
    departments_reused: int
    arms_created: int
    arms_reused: int
    class_groups_created: int
    class_groups_reused: int
    message: str


class ClassGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_session_id: UUID
    academic_level_id: UUID
    department_id: UUID | None = None
    class_arm_id: UUID
    class_teacher_id: UUID | None = None
    display_name: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
