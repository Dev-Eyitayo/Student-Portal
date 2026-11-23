from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.common.schemas import PageMeta


class LevelCategory(StrEnum):
    PRIMARY = "PRIMARY"
    JUNIOR_SECONDARY = "JUNIOR_SECONDARY"
    SENIOR_SECONDARY = "SENIOR_SECONDARY"


class StructureTemplateType(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    COMBINED = "COMBINED"


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
    status: str = "DRAFT"
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
    template_id: str | None = Field(None, min_length=2, max_length=64)
    template_type: StructureTemplateType = StructureTemplateType.SECONDARY
    academic_session_id: UUID | None = None
    session_name: str | None = Field(None, min_length=2, max_length=32)
    session_start_date: date | None = None
    session_end_date: date | None = None
    include_class_groups: bool = True
    separate_senior_departments: bool = True
    arms_per_level: int | None = Field(None, ge=1, le=26)
    department_arm_counts: dict[str, int] | None = None
    level_arm_counts: dict[str, int] | None = None
    level_department_arm_counts: dict[str, dict[str, int]] | None = None
    selected_subject_codes: list[str] | None = None

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
    template_type: StructureTemplateType
    academic_session_id: UUID
    levels_created: int
    levels_reused: int
    departments_created: int
    departments_reused: int
    arms_created: int
    arms_reused: int
    class_groups_created: int
    class_groups_reused: int
    subjects_created: int = 0
    subjects_reused: int = 0
    subject_offerings_created: int = 0
    subject_offerings_reused: int = 0
    class_subjects_created: int = 0
    class_subjects_reused: int = 0
    subject_registrations_created: int = 0
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
    class_teacher_name: str | None = None
    student_count: int = 0
    academic_session_name: str | None = None
    academic_level_name: str | None = None
    department_name: str | None = None
    class_arm_name: str | None = None
    display_name: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class DashboardAttentionItem(BaseModel):
    id: str
    issue_type: str
    title: str
    subtitle: str
    action_label: str
    target_route: str
    class_group_id: UUID | None = None
    class_subject_id: UUID | None = None
    academic_term_id: UUID | None = None


class DashboardRecentActivityItem(BaseModel):
    id: UUID
    action: str
    resource_type: str
    resource_id: UUID
    actor_name: str | None = None
    timestamp: datetime


class TermWorkflowStep(BaseModel):
    key: str
    label: str
    status: str
    detail: str


class AdminDashboardResponse(BaseModel):
    active_session_id: UUID | None = None
    active_session_name: str | None = None
    current_term_id: UUID | None = None
    current_term_name: str | None = None
    term_status: str | None = None
    students_enrolled: int
    active_staff: int
    score_sheets_approved: int
    score_sheets_total: int
    total_issues: int
    term_workflow: list[TermWorkflowStep]
    needs_attention: list[DashboardAttentionItem]
    recent_activities: list[DashboardRecentActivityItem]


class DashboardIssuesPageResponse(BaseModel):
    items: list[DashboardAttentionItem]
    meta: PageMeta


class TeacherClassSummary(BaseModel):
    class_group_id: UUID
    display_name: str
    department_name: str | None = None
    student_count: int
    today_attendance_marked: bool
    marked_count: int


class TeacherSubjectSummary(BaseModel):
    class_subject_id: UUID
    class_group_id: UUID
    class_group_name: str
    department_name: str | None = None
    subject_id: UUID
    subject_name: str
    subject_code: str
    approval_status: str
    target_route: str


class TeacherDashboardResponse(BaseModel):
    current_term_id: UUID | None = None
    current_term_name: str | None = None
    my_classes: list[TeacherClassSummary]
    subjects_count: int
    pending_score_sheets: int
    teaching_subjects: list[TeacherSubjectSummary]


class SessionRolloverRequest(BaseModel):
    source_session_id: UUID
    copy_class_groups: bool = True
    copy_class_teachers: bool = True
    copy_subject_offerings: bool = True
    copy_class_subjects: bool = True
    copy_subject_teachers: bool = True


class SessionRolloverResponse(BaseModel):
    source_session_id: UUID
    target_session_id: UUID
    source_session_name: str
    target_session_name: str
    class_groups_created: int = 0
    class_groups_reused: int = 0
    subject_offerings_created: int = 0
    subject_offerings_reused: int = 0
    class_subjects_created: int = 0
    class_subjects_reused: int = 0
    teacher_assignments_created: int = 0
    skipped_inactive_teachers: int = 0
    message: str
