from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.common.schemas import PageMeta


class AssessmentType(StrEnum):
    CONTINUOUS_ASSESSMENT = "CONTINUOUS_ASSESSMENT"
    EXAMINATION = "EXAMINATION"
    PRACTICAL = "PRACTICAL"


class AssessmentDefinitionCreateRequest(BaseModel):
    academic_term_id: UUID
    class_subject_id: UUID
    name: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=1, max_length=16)
    assessment_type: AssessmentType
    max_score: Decimal = Field(..., gt=0, max_digits=5, decimal_places=2)
    weight_percentage: Decimal = Field(..., ge=0, le=100, max_digits=5, decimal_places=2)
    sequence_order: int = Field(default=1, ge=1)


class AssessmentDefinitionUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    max_score: Decimal | None = Field(None, gt=0, max_digits=5, decimal_places=2)
    weight_percentage: Decimal | None = Field(None, ge=0, le=100, max_digits=5, decimal_places=2)
    sequence_order: int | None = Field(None, ge=1)


class AssessmentDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_term_id: UUID
    class_subject_id: UUID
    name: str
    code: str
    assessment_type: AssessmentType
    max_score: Decimal
    weight_percentage: Decimal
    sequence_order: int
    created_at: datetime


class ScoreStatus(StrEnum):
    SCORED = "SCORED"
    ABSENT = "ABSENT"
    EXEMPT = "EXEMPT"
    PENDING = "PENDING"


class ScoreEntryItem(BaseModel):
    student_enrollment_id: UUID
    score_status: ScoreStatus = ScoreStatus.SCORED
    raw_score: Decimal | None = Field(None, ge=0, max_digits=5, decimal_places=2)

    @model_validator(mode="after")
    def validate_score_status_consistency(self) -> "ScoreEntryItem":
        if self.score_status == ScoreStatus.SCORED and self.raw_score is None:
            raise ValueError("raw_score is mandatory when score_status is 'SCORED'.")
        if self.score_status != ScoreStatus.SCORED and self.raw_score is not None:
            raise ValueError(f"raw_score must be null when score_status is '{self.score_status}'.")
        return self


class BulkScoreEntryRequest(BaseModel):
    class_subject_id: UUID
    assessment_definition_id: UUID
    scores: list[ScoreEntryItem] = Field(..., min_length=1)


class ScoreEntryError(BaseModel):
    student_enrollment_id: UUID
    message: str


class BulkScoreEntryAssessmentSummary(BaseModel):
    assessment_name: str
    max_score: Decimal
    class_subject: str


class BulkScoreEntryResponse(BaseModel):
    records_processed: int
    records_updated: int
    errors: list[ScoreEntryError]
    assessment_summary: BulkScoreEntryAssessmentSummary


class ScoreSheetRow(BaseModel):
    student_enrollment_id: UUID
    admission_number: str
    first_name: str
    last_name: str
    score_status: ScoreStatus
    raw_score: Decimal | None = None


class ScoreSheetResponse(BaseModel):
    assessment_definition: AssessmentDefinitionResponse
    rows: list[ScoreSheetRow]


class ScoreSheetActionRequest(BaseModel):
    academic_term_id: UUID
    class_subject_id: UUID


class ScoreSheetUnlockRequest(ScoreSheetActionRequest):
    reason: str = Field(..., min_length=5, max_length=500)


class ScoreSheetStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_term_id: UUID
    class_subject_id: UUID
    status: str
    submitted_by_id: UUID | None = None
    submitted_at: datetime | None = None
    approved_by_id: UUID | None = None
    approved_at: datetime | None = None
    unlocked_by_id: UUID | None = None
    unlocked_at: datetime | None = None
    unlock_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ScoreSheetOverviewItem(BaseModel):
    class_subject_id: UUID
    academic_term_id: UUID
    class_group_id: UUID
    class_group_name: str
    subject_id: UUID
    subject_name: str
    subject_code: str
    teacher_names: list[str] = Field(default_factory=list)
    status: str
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    unlocked_at: datetime | None = None
    unlock_reason: str | None = None


class ScoreSheetOverviewSummary(BaseModel):
    total: int
    draft: int
    submitted: int
    approved: int


class ScoreSheetOverviewPageResponse(BaseModel):
    items: list[ScoreSheetOverviewItem]
    meta: PageMeta
    summary: ScoreSheetOverviewSummary
