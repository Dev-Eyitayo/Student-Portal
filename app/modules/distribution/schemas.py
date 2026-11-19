from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class ReportSchoolInfo(BaseModel):
    name: str
    school_type: str
    address: str
    city: str
    state: str
    logo_url: str | None = None
    stamp_url: str | None = None
    principal_signature_url: str | None = None
    report_card_template_id: str


class ReportStudentInfo(BaseModel):
    admission_number: str
    full_name: str
    academic_level: str
    department: str | None = None
    class_group: str
    academic_session: str
    term: str


class ReportAttendanceSummary(BaseModel):
    times_school_opened: int
    times_present: int
    times_absent: int


class ReportSummaryInfo(BaseModel):
    total_subjects: int
    total_obtainable: Decimal
    total_obtained: Decimal
    percentage: Decimal
    class_position: int | None = None
    total_students_in_arm: int
    class_teacher_comment: str | None = None
    principal_comment: str | None = None
    annual_cumulative_score: Decimal | None = None
    promotion_decision: str | None = None


class ReportSubjectItem(BaseModel):
    subject_code: str
    subject_name: str
    test_score: Decimal
    exam_score: Decimal
    total_score: Decimal
    grade: str
    remark: str
    subject_position: int | None = None
    class_average: Decimal
    first_term_score: Decimal | None = None
    second_term_score: Decimal | None = None
    third_term_score: Decimal | None = None
    cumulative_annual_score: Decimal | None = None
    cumulative_annual_grade: str | None = None


class ReportVerificationInfo(BaseModel):
    qr_payload: str
    issued_at: datetime
    is_authentic: bool


class VerifiedReportCardResponse(BaseModel):
    school: ReportSchoolInfo
    student: ReportStudentInfo
    attendance: ReportAttendanceSummary
    summary: ReportSummaryInfo
    subject_breakdown: list[ReportSubjectItem]
    verification: ReportVerificationInfo


class QrVerifyResponse(BaseModel):
    is_authentic: bool
    admission_number: str
    term_code: str
    total_obtained: str


class ReportCardRemarksUpdateRequest(BaseModel):
    teacher_comment: str | None = Field(None, max_length=1000)
    principal_comment: str | None = Field(None, max_length=1000)


class ReportCardRemarksResponse(BaseModel):
    id: UUID
    student_enrollment_id: UUID
    academic_term_id: UUID
    teacher_comment: str | None
    principal_comment: str | None


class NotifyResultLinkItem(BaseModel):
    student_enrollment_id: UUID
    token: str = Field(..., min_length=1)


class NotifyTermResultsRequest(BaseModel):
    academic_term_id: UUID
    result_links: list[NotifyResultLinkItem] = Field(..., min_length=1)


class NotifyTermResultsResponse(BaseModel):
    academic_term_id: UUID
    notifications_queued: int
    message: str


class ReportCardListItem(BaseModel):
    id: UUID
    student_enrollment_id: UUID
    student_id: UUID
    admission_number: str
    student_name: str
    cumulative_average_percentage: str | None = None
    class_position: int | None = None
    promotion_decision: str | None = None
    teacher_comment: str | None = None
    principal_comment: str | None = None
    publication_status: str


class ReportCardListSummary(BaseModel):
    students_computed: int
    class_average: str | None = None
    pass_rate: float | None = None
    term_status: str


class ReportCardListResponse(BaseModel):
    items: list[ReportCardListItem]
    summary: ReportCardListSummary
