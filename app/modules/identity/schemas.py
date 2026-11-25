from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.modules.common.schemas import PageMeta
from app.modules.common.validators import validate_optional_nigerian_phone


class UserRole(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    SCHOOL_ADMIN = "SCHOOL_ADMIN"
    ACADEMIC_OFFICER = "ACADEMIC_OFFICER"
    TEACHER = "TEACHER"


class StaffRole(StrEnum):
    SCHOOL_ADMIN = "SCHOOL_ADMIN"
    ACADEMIC_OFFICER = "ACADEMIC_OFFICER"
    TEACHER = "TEACHER"


class PermissionScopeType(StrEnum):
    SCHOOL_WIDE = "SCHOOL_WIDE"
    DEPARTMENT = "DEPARTMENT"
    ACADEMIC_LEVEL = "ACADEMIC_LEVEL"
    CLASS_GROUP = "CLASS_GROUP"


class SchoolType(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    COMBINED = "COMBINED"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class StaffProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    staff_id_number: str
    employment_status: str
    specialization: str | None = None
    signature_image_url: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID | None = None
    email: EmailStr
    first_name: str
    last_name: str
    phone_number: str | None = None
    role: str
    is_active: bool
    is_mfa_enabled: bool
    staff_profile: StaffProfileResponse | None = None


class AssignedClassGroupSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    academic_session_id: UUID
    academic_level_id: UUID
    department_id: UUID | None = None
    class_arm_id: UUID


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID | None = None
    school_name: str | None = None
    school_type: str | None = None
    email: EmailStr
    first_name: str
    last_name: str
    phone_number: str | None = None
    role: str
    is_active: bool
    is_mfa_enabled: bool
    staff_profile: StaffProfileResponse | None = None
    permissions: list[str] = Field(default_factory=list)
    assigned_class_groups: list[AssignedClassGroupSummary] = Field(default_factory=list)


class StaffInviteRequest(BaseModel):
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=64)
    last_name: str = Field(..., min_length=1, max_length=64)
    phone_number: str | None = Field(None, max_length=32)
    role: str = Field(default=StaffRole.TEACHER.value, min_length=1, max_length=32)
    staff_id_number: str = Field(..., min_length=1, max_length=64)
    specialization: str | None = Field(None, max_length=128)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, value: Any) -> str | None:
        return validate_optional_nigerian_phone(value)


class StaffInviteResponse(BaseModel):
    email: EmailStr
    role: str
    invitation_token: str
    expires_at: datetime
    message: str


class StaffVerifyInviteResponse(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    school_name: str
    role: str


class AcceptInvitationRequest(BaseModel):
    invitation_token: str
    password: str = Field(..., min_length=8)
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self) -> "AcceptInvitationRequest":
        if self.password != self.confirm_password:
            raise ValueError("The passwords entered do not match. Please re-enter them carefully.")
        return self


class PermissionGrantRequest(BaseModel):
    permission_code: str
    scope_type: PermissionScopeType = PermissionScopeType.SCHOOL_WIDE
    department_id: UUID | None = None
    academic_level_id: UUID | None = None
    class_group_id: UUID | None = None


class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    category: str
    description: str | None = None


class UserPermissionGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    permission_id: UUID
    permission_code: str | None = None
    scope_type: PermissionScopeType
    department_id: UUID | None = None
    academic_level_id: UUID | None = None
    class_group_id: UUID | None = None
    created_at: datetime


class DeactivateUserResponse(BaseModel):
    user_id: UUID
    is_active: bool
    message: str


class SchoolCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=63)
    official_code: str = Field(..., min_length=2, max_length=32)
    state: str = Field(..., min_length=2, max_length=64)
    city: str = Field(..., min_length=2, max_length=64)
    address: str = Field(..., min_length=5)
    country_code: str = Field("NG", max_length=2)
    admin_email: EmailStr
    admin_first_name: str = Field(..., min_length=1, max_length=64)
    admin_last_name: str = Field(..., min_length=1, max_length=64)
    admin_password: str = Field(..., min_length=8)
    school_type: SchoolType = SchoolType.SECONDARY


class SchoolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    official_code: str
    country_code: str
    state: str
    city: str
    address: str
    school_type: SchoolType
    is_active: bool
    created_at: datetime


class StaffBulkImportAcceptedResponse(BaseModel):
    task_id: str
    message: str


class StaffBulkImportStatusResponse(BaseModel):
    task_id: str
    state: str
    result: dict[str, Any] | None = None


class GradingScaleEntry(BaseModel):
    grade: str = Field(..., min_length=1, max_length=8)
    min_score: Decimal = Field(..., ge=0, le=100, max_digits=5, decimal_places=2)
    max_score: Decimal = Field(..., ge=0, le=100, max_digits=5, decimal_places=2)
    grade_point: Decimal = Field(default=Decimal("0"), ge=0, le=4, max_digits=3, decimal_places=2)
    remark: str = Field(..., min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_score_range(self) -> "GradingScaleEntry":
        if self.max_score < self.min_score:
            raise ValueError("The maximum score cannot be lower than the minimum score.")
        return self


class GradingScaleSchema(BaseModel):
    grades: list[GradingScaleEntry] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_grade_coverage(self) -> "GradingScaleSchema":
        ordered = sorted(self.grades, key=lambda grade: grade.min_score)
        if ordered[0].min_score != Decimal("0"):
            raise ValueError("The grading scale must start at 0.")
        if ordered[-1].max_score != Decimal("100"):
            raise ValueError("The grading scale must end at 100.")
        for index, grade in enumerate(ordered):
            if grade.max_score < grade.min_score:
                raise ValueError("The maximum score cannot be lower than the minimum score.")
            if index == 0:
                continue
            previous = ordered[index - 1]
            if grade.min_score <= previous.max_score:
                raise ValueError("Grading scale ranges cannot overlap.")
            if grade.min_score - previous.max_score != Decimal("0.01"):
                raise ValueError("Grading scale ranges cannot have gaps between them.")
        return self


class AssessmentComponent(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    max_score: Decimal = Field(..., gt=0, le=100, max_digits=5, decimal_places=2)
    weight_percentage: Decimal = Field(..., gt=0, le=100, max_digits=5, decimal_places=2)


class AssessmentWeightSchema(BaseModel):
    components: dict[str, AssessmentComponent] = Field(..., min_length=1)

    @model_validator(mode="before")
    def normalize_components(cls, data: Any) -> Any:
        if isinstance(data, dict) and "components" not in data:
            return {"components": data}
        return data

    @model_validator(mode="after")
    def validate_totals(self) -> "AssessmentWeightSchema":
        max_score_total = sum(item.max_score for item in self.components.values())
        weight_total = sum(item.weight_percentage for item in self.components.values())
        if max_score_total != Decimal("100"):
            raise ValueError("Assessment maximum scores must add up to 100.")
        if weight_total != Decimal("100"):
            raise ValueError("Assessment weight percentages must add up to 100.")
        return self


class AcademicTermDefinition(BaseModel):
    number: int = Field(..., ge=1, le=20)
    name: str = Field(..., min_length=2, max_length=32)


class AcademicTermDefinitionsSchema(BaseModel):
    terms: list[AcademicTermDefinition] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_terms(self) -> "AcademicTermDefinitionsSchema":
        numbers = [term.number for term in self.terms]
        names = [term.name.lower().strip() for term in self.terms]
        if len(set(numbers)) != len(numbers):
            raise ValueError("Each term must have a unique number.")
        if len(set(names)) != len(names):
            raise ValueError("Each term must have a unique name.")
        return self


class PromotionThresholdSchema(BaseModel):
    promoted_min_percentage: Decimal = Field(..., ge=0, le=100, max_digits=5, decimal_places=2)
    trial_min_percentage: Decimal = Field(..., ge=0, le=100, max_digits=5, decimal_places=2)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "PromotionThresholdSchema":
        if self.trial_min_percentage > self.promoted_min_percentage:
            raise ValueError("The trial promotion threshold cannot exceed the promotion threshold.")
        return self


class AnnualResultsConfig(BaseModel):
    version: int = Field(..., ge=1)
    term_weights: dict[str, int]
    promotion_thresholds: PromotionThresholdSchema

    @model_validator(mode="after")
    def validate_term_weights(self) -> "AnnualResultsConfig":
        expected = {"first_term", "second_term", "third_term"}
        if set(self.term_weights) != expected:
            raise ValueError("Term weights must include first term, second term, and third term.")
        if any(weight <= 0 for weight in self.term_weights.values()):
            raise ValueError("Every term weight must be greater than zero.")
        return self


class SchoolConfigurationUpdateRequest(BaseModel):
    grading_scale_schema: GradingScaleSchema | None = None
    assessment_weight_schema: AssessmentWeightSchema | None = None
    academic_term_definitions: AcademicTermDefinitionsSchema | None = None
    annual_results_schema: AnnualResultsConfig | None = None
    timezone: str | None = Field(None, min_length=3, max_length=64)
    currency: str | None = Field(None, min_length=3, max_length=3)
    report_card_template_id: str | None = Field(None, min_length=2, max_length=64)
    show_department_position_on_report_card: bool | None = None
    show_level_position_on_report_card: bool | None = None
    show_subject_position_on_report_card: bool | None = None
    setup_wizard_completed: bool | None = None
    setup_wizard_dismissed: bool | None = None


class SchoolConfigurationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    grading_scale_schema: dict[str, Any]
    assessment_weight_schema: dict[str, Any]
    academic_term_definitions: dict[str, Any]
    annual_results_schema: AnnualResultsConfig
    timezone: str
    currency: str
    report_card_template_id: str
    show_department_position_on_report_card: bool = False
    show_level_position_on_report_card: bool = False
    show_subject_position_on_report_card: bool = False
    setup_wizard_completed: bool = False
    setup_wizard_dismissed: bool = False
    created_at: datetime
    updated_at: datetime


class SchoolProfileUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    slug: str | None = Field(None, min_length=2, max_length=63)
    official_code: str | None = Field(None, min_length=2, max_length=32)
    state: str | None = Field(None, min_length=2, max_length=64)
    city: str | None = Field(None, min_length=2, max_length=64)
    address: str | None = Field(None, min_length=5)
    country_code: str | None = Field(None, min_length=2, max_length=2)
    school_type: SchoolType | None = None


class SchoolBrandingPaths(BaseModel):
    logo_storage_path: str | None = Field(None, max_length=512)
    stamp_image_url: str | None = Field(None, max_length=512)
    principal_signature_url: str | None = Field(None, max_length=512)


class SchoolBrandingResponse(BaseModel):
    logo_storage_path: str | None = None
    logo_url: str | None = None
    stamp_storage_path: str | None = None
    stamp_url: str | None = None
    principal_signature_storage_path: str | None = None
    principal_signature_url: str | None = None


class CustomDomainStatusResponse(BaseModel):
    enabled: bool = False
    status: str = "PLANNED_POST_PILOT"
    message: str = "Custom domains will be available after the pilot programme."


class SchoolSetupResponse(BaseModel):
    school: SchoolResponse
    configuration: SchoolConfigurationResponse
    branding: SchoolBrandingResponse
    structure_setup: dict[str, Any]
    custom_domain: CustomDomainStatusResponse


class SchoolSetupUpdateRequest(BaseModel):
    profile: SchoolProfileUpdateRequest | None = None
    configuration: SchoolConfigurationUpdateRequest | None = None
    branding: SchoolBrandingPaths | None = None


class BrandingAssetType(StrEnum):
    LOGO = "logo"
    STAMP = "stamp"
    PRINCIPAL_SIGNATURE = "principal_signature"


class BrandingUploadResponse(BaseModel):
    asset_type: BrandingAssetType
    storage_path: str
    url: str


class OnboardingTemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    school_type: SchoolType
    configuration_defaults: dict[str, Any]
    structure_template: dict[str, Any]
    custom_domain: CustomDomainStatusResponse


class StaffListItemResponse(BaseModel):
    id: UUID
    email: str
    first_name: str
    last_name: str
    phone_number: str | None = None
    role: str
    is_active: bool
    employment_status: str
    staff_id_number: str | None = None
    specialization: str | None = None
    last_login_at: datetime | None = None
    created_at: datetime


class StaffDirectorySummary(BaseModel):
    total: int
    teachers: int
    pending_invites: int
    deactivated: int


class StaffDirectoryPageResponse(BaseModel):
    items: list[StaffListItemResponse]
    meta: PageMeta
    summary: StaffDirectorySummary


class PermissionCatalogItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None = None
    category: str


class UserPermissionGrantDetailResponse(BaseModel):
    id: UUID
    permission_id: UUID
    permission_code: str
    permission_name: str
    scope_type: str
    department_id: UUID | None = None
    academic_level_id: UUID | None = None
    class_group_id: UUID | None = None
    created_at: datetime


class MeUpdateRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=64)
    last_name: str | None = Field(default=None, min_length=1, max_length=64)
    phone_number: str | None = Field(default=None, max_length=32)

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_non_blank_name(cls, value: str | None) -> str | None:
        if value is not None:
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("Name cannot be blank.")
            return cleaned
        return value

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, value: Any) -> str | None:
        return validate_optional_nigerian_phone(value)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class StaffUpdateRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=64)
    last_name: str | None = Field(default=None, min_length=1, max_length=64)
    phone_number: str | None = Field(default=None, max_length=32)
    role: str | None = Field(default=None, min_length=1, max_length=32)
    staff_id_number: str | None = Field(default=None, min_length=1, max_length=64)
    specialization: str | None = Field(default=None, max_length=128)

    @field_validator("first_name", "last_name", "staff_id_number")
    @classmethod
    def strip_non_blank(cls, value: str | None) -> str | None:
        if value is not None:
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("This field cannot be blank.")
            return cleaned
        return value

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, value: Any) -> str | None:
        return validate_optional_nigerian_phone(value)


class RolePermissionsResponse(BaseModel):
    role: str
    name: str
    description: str | None = None
    is_system: bool = True
    permission_codes: list[str]
    is_customized: bool


class CreateSchoolRoleRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permission_codes: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def strip_role_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Role name must be at least 2 characters.")
        return cleaned


class UpdateRolePermissionsRequest(BaseModel):
    permission_codes: list[str]


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class ResetPasswordVerifyResponse(BaseModel):
    valid: bool = True


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
