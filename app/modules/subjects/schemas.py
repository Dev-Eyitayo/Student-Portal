from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    code: str = Field(..., min_length=1, max_length=16)
    is_elective: bool = False
    is_trade_subject: bool = False


class SubjectUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=128)
    code: str | None = Field(None, min_length=1, max_length=16)
    is_elective: bool | None = None
    is_trade_subject: bool | None = None


class SubjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    code: str
    is_elective: bool
    is_trade_subject: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class OfferingCreateRequest(BaseModel):
    subject_id: UUID
    academic_session_id: UUID
    academic_level_id: UUID
    department_id: UUID | None = None


class OfferingUpdateRequest(BaseModel):
    department_id: UUID | None = None


class OfferingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    subject_id: UUID
    academic_session_id: UUID
    academic_level_id: UUID
    department_id: UUID | None = None
    subject_name: str | None = None
    subject_code: str | None = None
    academic_level_name: str | None = None
    department_name: str | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class ClassSubjectCreateRequest(BaseModel):
    subject_offering_id: UUID
    class_group_id: UUID


class ClassSubjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    subject_offering_id: UUID
    class_group_id: UUID
    subject_id: UUID | None = None
    subject_name: str | None = None
    subject_code: str | None = None
    category: str | None = None
    class_group_name: str | None = None
    teacher_names: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class SubjectSummaryResponse(BaseModel):
    total_subjects: int
    class_allocations: int
    without_teacher: int
    elective_or_trade: int


class TeacherAssignmentCreateRequest(BaseModel):
    teacher_id: UUID


class TeacherAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    teacher_id: UUID
    class_subject_id: UUID
    assigned_by_id: UUID
    is_active: bool
    start_date: date
    end_date: date | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class TeacherAssignmentBulkCreateRequest(BaseModel):
    subject_offering_id: UUID
    teacher_id: UUID
    class_group_ids: list[UUID] = Field(..., min_length=1)


class TeacherAssignmentBulkSkip(BaseModel):
    class_group_id: UUID
    reason: str


class TeacherAssignmentBulkResponse(BaseModel):
    assigned: list[TeacherAssignmentResponse]
    skipped: list[TeacherAssignmentBulkSkip]


class SubjectRegistrationCreateRequest(BaseModel):
    class_subject_id: UUID


class SubjectRegistrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    student_enrollment_id: UUID
    class_subject_id: UUID
    subject_category: str
    is_active: bool
    created_at: datetime


class SubjectRegistrationBulkCreateRequest(BaseModel):
    student_enrollment_ids: list[UUID] = Field(..., min_length=1)


class SubjectRegistrationBulkSkip(BaseModel):
    student_enrollment_id: UUID
    reason: str


class SubjectRegistrationBulkResponse(BaseModel):
    registered: list[SubjectRegistrationResponse]
    skipped: list[SubjectRegistrationBulkSkip]


class MyTeachingAssignmentResponse(BaseModel):
    assignment_id: UUID
    class_subject_id: UUID
    class_group_id: UUID
    class_group_name: str
    academic_session_id: UUID
    subject_id: UUID
    subject_name: str
    subject_code: str
    is_elective: bool
    is_trade_subject: bool
    start_date: date


class ClassSubjectTeacherSummary(BaseModel):
    assignment_id: UUID
    teacher_id: UUID
    first_name: str
    last_name: str
    email: str
    start_date: date


class TeacherSubjectQualificationsUpdateRequest(BaseModel):
    subject_ids: list[UUID] = Field(default_factory=list)
    level_categories: list[Literal["PRIMARY", "JUNIOR_SECONDARY", "SENIOR_SECONDARY"]] | None = None


class TeacherSubjectQualificationsResponse(BaseModel):
    teacher_id: UUID
    level_categories: list[str] = Field(default_factory=list)
    subjects: list[SubjectResponse] = Field(default_factory=list)


class QualifiedTeacherResponse(BaseModel):
    teacher_id: UUID
    first_name: str
    last_name: str
    email: str
    level_categories: list[str] = Field(default_factory=list)


class ClassGroupSubjectItem(BaseModel):
    class_subject_id: UUID
    subject_offering_id: UUID
    subject_id: UUID
    subject_name: str
    subject_code: str
    is_elective: bool
    is_trade_subject: bool
    teachers: list[ClassSubjectTeacherSummary] = Field(default_factory=list)
