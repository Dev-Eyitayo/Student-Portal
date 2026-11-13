from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


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
    refresh_token: Optional[str] = None


class StaffProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    staff_id_number: str
    employment_status: str
    specialization: Optional[str] = None
    signature_image_url: Optional[str] = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: Optional[UUID] = None
    email: EmailStr
    first_name: str
    last_name: str
    phone_number: Optional[str] = None
    role: str
    is_active: bool
    is_mfa_enabled: bool
    staff_profile: Optional[StaffProfileResponse] = None


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: Optional[UUID] = None
    school_name: Optional[str] = None
    email: EmailStr
    first_name: str
    last_name: str
    phone_number: Optional[str] = None
    role: str
    is_active: bool
    is_mfa_enabled: bool
    staff_profile: Optional[StaffProfileResponse] = None
    permissions: List[str] = Field(default_factory=list)


class StaffInviteRequest(BaseModel):
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=64)
    last_name: str = Field(..., min_length=1, max_length=64)
    phone_number: Optional[str] = Field(None, max_length=32)
    role: str = Field("TEACHER", description="Role: TEACHER, ACADEMIC_OFFICER, SCHOOL_ADMIN")
    staff_id_number: str = Field(..., min_length=1, max_length=64)
    specialization: Optional[str] = Field(None, max_length=128)


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
    scope_type: str = Field("SCHOOL_WIDE", description="SCHOOL_WIDE, DEPARTMENT, ACADEMIC_LEVEL, CLASS_GROUP")
    department_id: Optional[UUID] = None
    academic_level_id: Optional[UUID] = None
    class_group_id: Optional[UUID] = None


class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    category: str
    description: Optional[str] = None


class UserPermissionGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    permission_id: UUID
    permission_code: Optional[str] = None
    scope_type: str
    department_id: Optional[UUID] = None
    academic_level_id: Optional[UUID] = None
    class_group_id: Optional[UUID] = None
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
    is_active: bool
    created_at: datetime
