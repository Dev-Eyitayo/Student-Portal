from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.common.schemas import PageMeta


class PromotionDecision(StrEnum):
    PROMOTED = "PROMOTED"
    PROMOTED_ON_TRIAL = "PROMOTED_ON_TRIAL"
    REPEATING = "REPEATING"


class CalculateTermRequest(BaseModel):
    academic_term_id: UUID
    class_group_id: UUID | None = None


class ComponentScoreResponse(BaseModel):
    code: str
    score_status: str
    raw_score: Decimal | None = None
    scaled_score: Decimal


class SubjectComputationResponse(BaseModel):
    class_subject_id: UUID
    subject_code: str
    subject_name: str
    total_score: Decimal
    obtainable_marks: Decimal
    grade: str
    grade_point: Decimal
    remark: str
    subject_position: int
    class_highest_score: Decimal
    class_lowest_score: Decimal
    class_average_score: Decimal
    components: list[ComponentScoreResponse]


class StudentBroadsheetRow(BaseModel):
    student_enrollment_id: UUID
    admission_number: str
    first_name: str
    last_name: str
    class_group_name: str | None = None
    subjects: list[SubjectComputationResponse]
    total_obtained: Decimal
    total_obtainable: Decimal
    percentage: Decimal
    class_position: int
    total_students_in_arm: int | None = None
    level_position: int | None = None
    total_students_in_level: int | None = None
    annual_score: Decimal | None = None
    promotion_decision: PromotionDecision | None = None


class BroadsheetResponse(BaseModel):
    academic_term_id: UUID
    class_group_id: UUID | None = None
    class_group_name: str
    academic_level_id: UUID | None = None
    academic_level_name: str | None = None
    department_name: str | None = None
    academic_term_name: str
    total_students: int
    students: list[StudentBroadsheetRow]
    meta: PageMeta | None = None


class CalculateTermResponse(BaseModel):
    academic_term_id: UUID
    class_group_id: UUID | None
    students_calculated: int
    subject_results_upserted: int


class PublishTermRequest(BaseModel):
    academic_term_id: UUID


class ResultLinkResponse(BaseModel):
    student_enrollment_id: UUID
    student_name: str | None = None
    admission_number: str | None = None
    class_group_name: str | None = None
    guardian_email: str | None = None
    token: str
    expires_at: datetime


class PublishTermResponse(BaseModel):
    academic_term_id: UUID
    published_at: datetime
    report_cards_published: int
    result_links: list[ResultLinkResponse]


class SubjectResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_term_id: UUID
    class_subject_id: UUID
    student_enrollment_id: UUID
    total_score: Decimal
    grade: str
    grade_point: Decimal | None = None
    remark: str
    subject_position: int | None = None
    status: str
    created_at: datetime
    updated_at: datetime
