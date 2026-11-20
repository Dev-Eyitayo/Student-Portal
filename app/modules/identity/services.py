import asyncio
import secrets
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import ConflictException, ResourceNotFoundException
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_token_revoked,
    verify_password,
)
from app.core.storage import storage_service
from app.modules.academic_structure.models import (
    AcademicLevel,
    AcademicSession,
    ClassArm,
    ClassGroup,
    Department,
)
from app.modules.academic_structure.templates import (
    PRIMARY_STRUCTURE_TEMPLATE_ID,
    SECONDARY_STRUCTURE_TEMPLATE_ID,
    get_structure_template_preview,
)
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.common.models import generate_uuid7
from app.modules.common.schemas import PageMeta
from app.modules.identity.bulk_import import ParsedStaffRow, StaffBulkImportRow
from app.modules.identity.models import (
    Permission,
    School,
    SchoolConfiguration,
    StaffProfile,
    User,
    UserPermissionGrant,
)
from app.modules.identity.schemas import (
    AcceptInvitationRequest,
    BrandingAssetType,
    OnboardingTemplateResponse,
    PermissionGrantRequest,
    SchoolBrandingPaths,
    SchoolBrandingResponse,
    SchoolConfigurationResponse,
    SchoolConfigurationUpdateRequest,
    SchoolCreateRequest,
    SchoolProfileUpdateRequest,
    SchoolResponse,
    SchoolSetupResponse,
    SchoolSetupUpdateRequest,
    SchoolType,
    StaffInviteRequest,
    StaffInviteResponse,
    StaffVerifyInviteResponse,
    TokenResponse,
)


async def _commit_or_conflict(
    db: AsyncSession,
    *,
    message: str,
    code: str = "CONFLICT",
) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictException(message=message, code=code) from exc


async def _scope_audit_to_school(db: AsyncSession, school_id: UUID) -> None:
    """Sets the RLS tenant session variable for the rest of the current transaction.

    register_school_with_admin and accept_staff_invitation run on a plain
    get_db() session with no prior tenant context (there is no authenticated
    user yet), so the engine's "begin" hook applies an empty school_id. The
    transaction is already open by the time we know the school_id, so it must
    be set directly rather than through set_tenant_scope (which only takes
    effect on the next transaction begin).
    """
    await db.execute(
        text("SELECT set_config('app.current_school_id', :school_id, true)"),
        {"school_id": str(school_id)},
    )


SECONDARY_ASSESSMENT_WEIGHTS = {
    "TEST1": {"name": "Test 1", "max_score": 10, "weight_percentage": 10},
    "TEST2": {"name": "Test 2", "max_score": 10, "weight_percentage": 10},
    "ASSIGN": {
        "name": "Assignments/Attendance",
        "max_score": 10,
        "weight_percentage": 10,
    },
    "EXAM": {"name": "Terminal Examination", "max_score": 70, "weight_percentage": 70},
}

PRIMARY_ASSESSMENT_WEIGHTS = {
    "TEST1": {"name": "Test 1", "max_score": 10, "weight_percentage": 10},
    "TEST2": {"name": "Test 2", "max_score": 10, "weight_percentage": 10},
    "ASSIGN": {
        "name": "Assignments/Attendance",
        "max_score": 10,
        "weight_percentage": 10,
    },
    "EXAM": {"name": "Terminal Examination", "max_score": 70, "weight_percentage": 70},
}

SECONDARY_GRADING_SCALE = [
    {"grade": "A1", "min_score": 75, "max_score": 100, "grade_point": 4, "remark": "Excellent"},
    {"grade": "B2", "min_score": 70, "max_score": 74.99, "grade_point": 3.5, "remark": "Very Good"},
    {"grade": "B3", "min_score": 65, "max_score": 69.99, "grade_point": 3, "remark": "Good"},
    {"grade": "C4", "min_score": 60, "max_score": 64.99, "grade_point": 2.5, "remark": "Credit"},
    {"grade": "C5", "min_score": 55, "max_score": 59.99, "grade_point": 2, "remark": "Credit"},
    {"grade": "C6", "min_score": 50, "max_score": 54.99, "grade_point": 1.5, "remark": "Credit"},
    {"grade": "D7", "min_score": 45, "max_score": 49.99, "grade_point": 1, "remark": "Pass"},
    {"grade": "E8", "min_score": 40, "max_score": 44.99, "grade_point": 0.5, "remark": "Pass"},
    {"grade": "F9", "min_score": 0, "max_score": 39.99, "grade_point": 0, "remark": "Fail"},
]

PRIMARY_GRADING_SCALE = [
    {"grade": "A", "min_score": 70, "max_score": 100, "grade_point": 0, "remark": "Excellent"},
    {"grade": "B", "min_score": 60, "max_score": 69.99, "grade_point": 0, "remark": "Very Good"},
    {"grade": "C", "min_score": 50, "max_score": 59.99, "grade_point": 0, "remark": "Good"},
    {"grade": "D", "min_score": 45, "max_score": 49.99, "grade_point": 0, "remark": "Pass"},
    {"grade": "E", "min_score": 40, "max_score": 44.99, "grade_point": 0, "remark": "Fair"},
    {"grade": "F", "min_score": 0, "max_score": 39.99, "grade_point": 0, "remark": "Fail"},
]

DEFAULT_TERM_DEFINITIONS = [
    {"number": 1, "name": "First Term"},
    {"number": 2, "name": "Second Term"},
    {"number": 3, "name": "Third Term"},
]

DEFAULT_ANNUAL_RESULTS_CONFIG = {
    "version": 1,
    "term_weights": {"first_term": 1, "second_term": 1, "third_term": 1},
    "promotion_thresholds": {
        "promoted_min_percentage": "50.00",
        "trial_min_percentage": "40.00",
    },
}

SECONDARY_REPORT_CARD_TEMPLATE_ID = "default_v1"
PRIMARY_REPORT_CARD_TEMPLATE_ID = "default_primary_v1"

SCHOOL_DEFAULTS: dict[SchoolType, dict[str, Any]] = {
    SchoolType.SECONDARY: {
        "grading_scale_schema": {"grades": SECONDARY_GRADING_SCALE},
        "assessment_weight_schema": SECONDARY_ASSESSMENT_WEIGHTS,
        "academic_term_definitions": {"terms": DEFAULT_TERM_DEFINITIONS},
        "annual_results_schema": DEFAULT_ANNUAL_RESULTS_CONFIG,
        "timezone": "Africa/Lagos",
        "currency": "NGN",
        "report_card_template_id": SECONDARY_REPORT_CARD_TEMPLATE_ID,
        "structure_template_id": SECONDARY_STRUCTURE_TEMPLATE_ID,
    },
    SchoolType.PRIMARY: {
        "grading_scale_schema": {"grades": PRIMARY_GRADING_SCALE},
        "assessment_weight_schema": PRIMARY_ASSESSMENT_WEIGHTS,
        "academic_term_definitions": {"terms": DEFAULT_TERM_DEFINITIONS},
        "annual_results_schema": DEFAULT_ANNUAL_RESULTS_CONFIG,
        "timezone": "Africa/Lagos",
        "currency": "NGN",
        "report_card_template_id": PRIMARY_REPORT_CARD_TEMPLATE_ID,
        "structure_template_id": PRIMARY_STRUCTURE_TEMPLATE_ID,
    },
}


def get_school_defaults(school_type: SchoolType | str) -> dict[str, Any]:
    normalized = SchoolType(school_type)
    return deepcopy(SCHOOL_DEFAULTS[normalized])


@dataclass
class StaffBulkProvisionResult:
    school_name: str
    created: int
    invitations: list[StaffInviteResponse]
    errors: list[dict[str, Any]]


def _build_staff_invitation(user: User, school_id: UUID) -> StaffInviteResponse:
    expires_at = datetime.now(UTC) + timedelta(hours=72)
    invitation_token = create_access_token(
        subject=str(user.id),
        school_id=school_id,
        role=user.role,
        expires_delta=timedelta(hours=72),
        extra_claims={
            "type": "invitation",
            "email": user.email,
            "school_id": str(school_id),
        },
    )
    return StaffInviteResponse(
        email=user.email,
        role=user.role,
        invitation_token=invitation_token,
        expires_at=expires_at,
        message="Staff invitation generated successfully. Share this invitation link with the teacher.",
    )


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    stmt = (
        select(User)
        .options(selectinload(User.staff_profile), selectinload(User.school))
        .where(User.email == email.lower().strip())
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not await asyncio.to_thread(verify_password, password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The email or password you entered is incorrect. Please check your details and try again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is currently inactive. Please reach out to your school administrator for assistance.",
        )

    if user.school is not None and not user.school.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your school's account is currently suspended. Please contact your platform administrator.",
        )

    user.last_login_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(user)
    return user


async def create_tokens_for_user(user: User) -> TokenResponse:
    extra_claims = {
        "school_id": str(user.school_id) if user.school_id else None,
        "role": user.role,
        "email": user.email,
    }
    access_token = create_access_token(
        subject=str(user.id),
        school_id=user.school_id,
        role=user.role,
        extra_claims=extra_claims,
    )
    refresh_token = create_refresh_token(
        subject=str(user.id),
        school_id=user.school_id,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


async def refresh_user_tokens(db: AsyncSession, refresh_token: str) -> TokenResponse:
    try:
        payload = decode_token(refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please sign in again.",
        ) from None

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token provided. Please sign in again.",
        )

    # A signed-out refresh token must not be exchangeable for a new session.
    jti = payload.get("jti")
    if jti and await is_token_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session has been signed out. Please sign in again to continue.",
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session data. Please sign in again.",
        )

    user_id = UUID(user_id_str)
    stmt = (
        select(User)
        .options(selectinload(User.staff_profile), selectinload(User.school))
        .where(User.id == user_id)
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This user account is no longer active. Please contact your school administrator.",
        )

    # Check token_valid_after. JWT `iat` has whole-second precision, so the
    # database timestamp must be truncated to the same precision before
    # comparing; otherwise a token issued in the same second as an account
    # change is incorrectly rejected.
    token_iat = datetime.fromtimestamp(payload.get("iat", 0), tz=UTC)
    if user.token_valid_after.replace(microsecond=0) > token_iat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has been invalidated because your security settings changed. Please sign in again.",
        )

    return await create_tokens_for_user(user)


async def register_school_with_admin(
    db: AsyncSession, req: SchoolCreateRequest, actor_ip: str = "0.0.0.0"
) -> tuple[School, User]:
    # Check uniqueness of slug and official code
    slug_check = await db.execute(select(School).where(School.slug == req.slug.lower().strip()))
    if slug_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The school web address slug '{req.slug}' is already taken. Please choose another one.",
        )

    code_check = await db.execute(
        select(School).where(School.official_code == req.official_code.upper().strip())
    )
    if code_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A school with official code '{req.official_code}' already exists.",
        )

    email_check = await db.execute(
        select(User).where(User.email == req.admin_email.lower().strip())
    )
    if email_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account with email '{req.admin_email}' is already registered in the system.",
        )

    school_type = SchoolType(req.school_type)
    defaults = get_school_defaults(school_type)

    # Create School
    school = School(
        slug=req.slug.lower().strip(),
        name=req.name.strip(),
        official_code=req.official_code.upper().strip(),
        country_code=req.country_code.upper().strip(),
        state=req.state.strip(),
        city=req.city.strip(),
        address=req.address.strip(),
        school_type=school_type.value,
        is_active=True,
    )
    db.add(school)
    await db.flush()

    # Create default configuration
    config = SchoolConfiguration(
        school_id=school.id,
        grading_scale_schema=defaults["grading_scale_schema"],
        assessment_weight_schema=defaults["assessment_weight_schema"],
        academic_term_definitions=defaults["academic_term_definitions"],
        annual_results_schema=defaults["annual_results_schema"],
        timezone=defaults["timezone"],
        currency=defaults["currency"],
        report_card_template_id=defaults["report_card_template_id"],
    )
    db.add(config)

    # Create Initial Admin User
    admin_user = User(
        school_id=school.id,
        email=req.admin_email.lower().strip(),
        password_hash=await asyncio.to_thread(hash_password, req.admin_password),
        first_name=req.admin_first_name.strip(),
        last_name=req.admin_last_name.strip(),
        role="SCHOOL_ADMIN",
        is_active=True,
    )
    db.add(admin_user)
    await db.flush()
    await _scope_audit_to_school(db, school.id)
    record_audit_log(
        db,
        AuditContext(school_id=school.id, actor_id=admin_user.id, actor_ip=actor_ip),
        action="SCHOOL_REGISTERED",
        resource_type="School",
        resource_id=school.id,
        diff_payload={
            "after": {
                "name": school.name,
                "official_code": school.official_code,
                "school_type": school.school_type,
            }
        },
    )
    await _commit_or_conflict(
        db,
        message=(
            "This school could not be registered because one of the details provided "
            "conflicts with an existing school or account."
        ),
        code="SCHOOL_REGISTRATION_CONFLICT",
    )
    await db.refresh(school)
    await db.refresh(admin_user)

    return school, admin_user


async def get_school_configuration(db: AsyncSession, school_id: UUID) -> SchoolConfiguration:
    stmt = select(SchoolConfiguration).where(SchoolConfiguration.school_id == school_id)
    config = (await db.execute(stmt)).scalar_one_or_none()
    if not config:
        raise ResourceNotFoundException("school configuration", school_id)
    return config


CONFIGURATION_FIELDS = (
    "grading_scale_schema",
    "assessment_weight_schema",
    "academic_term_definitions",
    "annual_results_schema",
    "timezone",
    "currency",
    "report_card_template_id",
)


def _configuration_snapshot(config: SchoolConfiguration) -> dict[str, Any]:
    return {field: getattr(config, field) for field in CONFIGURATION_FIELDS}


def _apply_configuration_updates(
    config: SchoolConfiguration, req: SchoolConfigurationUpdateRequest
) -> None:
    changes = req.model_dump(mode="json", exclude_unset=True)
    assessment_weights = changes.get("assessment_weight_schema")
    if isinstance(assessment_weights, dict) and "components" in assessment_weights:
        changes["assessment_weight_schema"] = assessment_weights["components"]
    for field in CONFIGURATION_FIELDS:
        if field in changes and changes[field] is not None:
            setattr(config, field, changes[field])


async def update_school_configuration(
    db: AsyncSession,
    school_id: UUID,
    req: SchoolConfigurationUpdateRequest,
    current_user: User,
    audit: AuditContext,
) -> SchoolConfiguration:
    config = await get_school_configuration(db, school_id)
    before = _configuration_snapshot(config)
    _apply_configuration_updates(config, req)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SCHOOL_CONFIGURATION_UPDATED",
        resource_type="SchoolConfiguration",
        resource_id=config.id,
        diff_payload={"before": before, "after": _configuration_snapshot(config)},
    )
    await _commit_or_conflict(
        db,
        message=(
            "These school settings could not be saved. Please check the values and try again."
        ),
        code="SCHOOL_CONFIGURATION_CONFLICT",
    )
    await db.refresh(config)
    return config


def get_onboarding_templates() -> list[OnboardingTemplateResponse]:
    templates: list[OnboardingTemplateResponse] = []
    catalog = [
        (
            "olivet_nigeria_default",
            "Olivet Nigeria Default",
            (
                "Olivet's familiar grading, 10/10/10/70 assessment pattern, three terms, "
                "and JSS-SS3 structure."
            ),
            SchoolType.SECONDARY,
            SECONDARY_STRUCTURE_TEMPLATE_ID,
        ),
        (
            "nigeria_primary_default",
            "Nigeria Primary Basic 1-6",
            (
                "Basic 1-6 with arms A-D, compulsory core subjects, an A-F grading scale, "
                "and the same 10/10/10/70 assessment pattern."
            ),
            SchoolType.PRIMARY,
            PRIMARY_STRUCTURE_TEMPLATE_ID,
        ),
    ]

    for template_id, name, description, school_type, structure_template_id in catalog:
        structure_template = get_structure_template_preview(structure_template_id)
        if structure_template is None:
            continue
        defaults = get_school_defaults(school_type)
        templates.append(
            OnboardingTemplateResponse(
                id=template_id,
                name=name,
                description=description,
                school_type=school_type,
                configuration_defaults=defaults,
                structure_template=structure_template,
                custom_domain={},
            )
        )
    return templates


async def _get_school_for_setup(db: AsyncSession, school_id: UUID) -> School:
    school = (await db.execute(select(School).where(School.id == school_id))).scalar_one_or_none()
    if school is None:
        raise ResourceNotFoundException("school", school_id)
    return school


async def _structure_setup_status(db: AsyncSession, school_id: UUID) -> dict[str, Any]:
    async def count(model: Any) -> int:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.school_id == school_id, model.deleted_at.is_(None))
                )
            ).scalar_one()
        )

    return {
        "academic_sessions": await count(AcademicSession),
        "academic_levels": await count(AcademicLevel),
        "departments": await count(Department),
        "class_arms": await count(ClassArm),
        "class_groups": await count(ClassGroup),
        "template_apply_available": True,
    }


def _branding_response(school: School) -> SchoolBrandingResponse:
    return SchoolBrandingResponse(
        logo_storage_path=school.logo_storage_path,
        logo_url=storage_service.public_url(school.logo_storage_path),
        stamp_storage_path=school.stamp_image_url,
        stamp_url=storage_service.public_url(school.stamp_image_url),
        principal_signature_storage_path=school.principal_signature_url,
        principal_signature_url=storage_service.public_url(school.principal_signature_url),
    )


async def get_school_setup(db: AsyncSession, school_id: UUID) -> SchoolSetupResponse:
    school = await _get_school_for_setup(db, school_id)
    config = await get_school_configuration(db, school_id)
    return SchoolSetupResponse(
        school=SchoolResponse.model_validate(school),
        configuration=SchoolConfigurationResponse.model_validate(config),
        branding=_branding_response(school),
        structure_setup=await _structure_setup_status(db, school_id),
        custom_domain={},
    )


async def _ensure_profile_field_available(
    db: AsyncSession,
    school_id: UUID,
    field: Any,
    value: str,
    friendly_name: str,
) -> None:
    existing = (
        await db.execute(select(School.id).where(field == value, School.id != school_id))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictException(
            f"That {friendly_name} is already in use by another school. Please choose a different one."
        )


async def update_school_setup(
    db: AsyncSession,
    school_id: UUID,
    req: SchoolSetupUpdateRequest,
    audit: AuditContext,
) -> SchoolSetupResponse:
    school = await _get_school_for_setup(db, school_id)
    config = await get_school_configuration(db, school_id)
    before: dict[str, Any] = {
        "school": {
            field: getattr(school, field)
            for field in (
                "name",
                "slug",
                "official_code",
                "state",
                "city",
                "address",
                "country_code",
                "school_type",
                "logo_storage_path",
                "stamp_image_url",
                "principal_signature_url",
            )
        },
        "configuration": _configuration_snapshot(config),
    }

    school_type_changed = False
    if req.profile is not None:
        profile: SchoolProfileUpdateRequest = req.profile
        if profile.school_type is not None:
            new_school_type = SchoolType(profile.school_type)
            if school.school_type != new_school_type.value:
                school.school_type = new_school_type.value
                school_type_changed = True
        if profile.slug is not None:
            normalized_slug = profile.slug.lower().strip()
            await _ensure_profile_field_available(
                db, school_id, School.slug, normalized_slug, "school web address"
            )
            school.slug = normalized_slug
        if profile.official_code is not None:
            normalized_code = profile.official_code.upper().strip()
            await _ensure_profile_field_available(
                db, school_id, School.official_code, normalized_code, "official school code"
            )
            school.official_code = normalized_code
        for field in ("name", "state", "city", "address", "country_code"):
            value = getattr(profile, field)
            if value is not None:
                setattr(
                    school,
                    field,
                    value.strip().upper() if field == "country_code" else value.strip(),
                )

    if req.branding is not None:
        branding: SchoolBrandingPaths = req.branding
        field_map = {
            "logo_storage_path": "logo_storage_path",
            "stamp_image_url": "stamp_image_url",
            "principal_signature_url": "principal_signature_url",
        }
        for field, value in branding.model_dump(exclude_unset=True).items():
            setattr(school, field_map[field], value)

    if req.configuration is not None:
        _apply_configuration_updates(config, req.configuration)
    elif school_type_changed:
        defaults = get_school_defaults(school.school_type)
        config.grading_scale_schema = defaults["grading_scale_schema"]
        config.assessment_weight_schema = defaults["assessment_weight_schema"]
        config.academic_term_definitions = defaults["academic_term_definitions"]
        config.annual_results_schema = defaults["annual_results_schema"]
        config.report_card_template_id = defaults["report_card_template_id"]

    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SCHOOL_SETUP_UPDATED",
        resource_type="School",
        resource_id=school.id,
        diff_payload={
            "before": before,
            "after": {
                "school": {field: getattr(school, field) for field in before["school"]},
                "configuration": _configuration_snapshot(config),
            },
        },
    )
    await _commit_or_conflict(
        db,
        message="Your onboarding setup could not be saved. Please check the values and try again.",
        code="SCHOOL_SETUP_CONFLICT",
    )
    return await get_school_setup(db, school_id)


async def save_school_branding_asset(
    db: AsyncSession,
    school_id: UUID,
    asset_type: BrandingAssetType,
    content_type: str,
    content: bytes,
    audit: AuditContext,
) -> str:
    school = await _get_school_for_setup(db, school_id)
    storage_path = await storage_service.save_school_branding_asset(
        school_slug=school.slug,
        asset_type=asset_type.value,
        content_type=content_type,
        content=content,
    )
    field_map = {
        BrandingAssetType.LOGO: "logo_storage_path",
        BrandingAssetType.STAMP: "stamp_image_url",
        BrandingAssetType.PRINCIPAL_SIGNATURE: "principal_signature_url",
    }
    field_name = field_map[asset_type]
    before = getattr(school, field_name)
    setattr(school, field_name, storage_path)
    await db.flush()
    record_audit_log(
        db,
        audit,
        action="SCHOOL_BRANDING_UPLOADED",
        resource_type="School",
        resource_id=school.id,
        diff_payload={"asset_type": asset_type.value, "before": before, "after": storage_path},
    )
    await _commit_or_conflict(
        db,
        message="The branding image could not be saved. Please try uploading it again.",
        code="SCHOOL_BRANDING_CONFLICT",
    )
    return storage_path


async def get_school_branding(db: AsyncSession, school_id: UUID) -> SchoolBrandingResponse:
    school = await _get_school_for_setup(db, school_id)
    return _branding_response(school)


async def invite_staff_member(
    db: AsyncSession,
    school_id: UUID,
    granter_id: UUID,
    req: StaffInviteRequest,
    actor_ip: str = "0.0.0.0",
) -> StaffInviteResponse:
    # Verify school exists
    school = await db.get(School, school_id)
    if not school:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The school associated with this request could not be found.",
        )

    # Check email uniqueness
    existing_user = await db.execute(select(User).where(User.email == req.email.lower().strip()))
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with email address '{req.email}' is already registered.",
        )

    # Check staff_id_number uniqueness within school
    existing_staff_num = await db.execute(
        select(StaffProfile).where(
            and_(
                StaffProfile.school_id == school_id,
                StaffProfile.staff_id_number == req.staff_id_number.strip(),
            )
        )
    )
    if existing_staff_num.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A staff member with ID '{req.staff_id_number}' already exists in your school.",
        )

    # Create inactive user waiting for invitation acceptance with a cryptographically random unusable hash
    unusable_random_secret = secrets.token_urlsafe(64)
    temporary_hash = await asyncio.to_thread(hash_password, unusable_random_secret)
    user = User(
        school_id=school_id,
        email=req.email.lower().strip(),
        password_hash=temporary_hash,
        first_name=req.first_name.strip(),
        last_name=req.last_name.strip(),
        phone_number=req.phone_number.strip() if req.phone_number else None,
        role=req.role.value,
        is_active=False,
    )
    db.add(user)
    await db.flush()

    staff_profile = StaffProfile(
        school_id=school_id,
        user_id=user.id,
        staff_id_number=req.staff_id_number.strip(),
        employment_status="INVITED",
        specialization=req.specialization.strip() if req.specialization else None,
    )
    db.add(staff_profile)
    record_audit_log(
        db,
        AuditContext(school_id=school_id, actor_id=granter_id, actor_ip=actor_ip),
        action="STAFF_INVITED",
        resource_type="User",
        resource_id=user.id,
        diff_payload={"after": {"email": user.email, "role": user.role}},
    )
    await _commit_or_conflict(
        db,
        message=(
            "This staff invitation could not be sent because the email address or staff ID "
            "conflicts with an existing record."
        ),
        code="STAFF_INVITE_CONFLICT",
    )

    return _build_staff_invitation(user, school_id)


async def provision_staff_batch(
    db: AsyncSession,
    school_id: UUID,
    granter_id: UUID,
    rows: list[ParsedStaffRow],
    actor_ip: str = "0.0.0.0",
) -> StaffBulkProvisionResult:
    """Validates and provisions a staff roster atomically.

    All rows are checked before any account is created. If any row fails,
    no users or staff profiles are committed and the caller receives a
    row-by-row error report.
    """
    school = await db.get(School, school_id)
    if not school:
        return StaffBulkProvisionResult(
            school_name="",
            created=0,
            invitations=[],
            errors=[
                {
                    "row": 0,
                    "message": (
                        "The school associated with this import could not be found. "
                        "Please check that you are signed in to the right school."
                    ),
                }
            ],
        )

    errors: list[dict[str, Any]] = []
    seen_emails: dict[str, int] = {}
    seen_staff_ids: dict[str, int] = {}
    normalized_rows: list[tuple[int, StaffBulkImportRow, str, str]] = []

    for parsed in rows:
        row = parsed.data
        email = row.email.lower()
        staff_id = row.staff_id_number

        if email in seen_emails:
            errors.append(
                {
                    "row": parsed.row_number,
                    "message": (
                        f"The email address {email} appears more than once in this "
                        f"file. It was first used on row {seen_emails[email]}."
                    ),
                }
            )
        else:
            seen_emails[email] = parsed.row_number

        if staff_id in seen_staff_ids:
            errors.append(
                {
                    "row": parsed.row_number,
                    "message": (
                        f"The staff ID {staff_id} appears more than once in this "
                        f"file. It was first used on row {seen_staff_ids[staff_id]}."
                    ),
                }
            )
        else:
            seen_staff_ids[staff_id] = parsed.row_number

        normalized_rows.append((parsed.row_number, row, email, staff_id))

    existing_emails = set(
        (await db.execute(select(User.email).where(User.email.in_(seen_emails)))).scalars().all()
    )
    existing_staff_ids = set(
        (
            await db.execute(
                select(StaffProfile.staff_id_number).where(
                    and_(
                        StaffProfile.school_id == school_id,
                        StaffProfile.staff_id_number.in_(seen_staff_ids),
                    )
                )
            )
        )
        .scalars()
        .all()
    )

    for row_number, _, email, staff_id in normalized_rows:
        if email in existing_emails:
            errors.append(
                {
                    "row": row_number,
                    "message": (
                        f"An account with the email address {email} already exists. "
                        "Please use a different email address."
                    ),
                }
            )
        if staff_id in existing_staff_ids:
            errors.append(
                {
                    "row": row_number,
                    "message": (
                        f"A staff member with ID {staff_id} already exists in your "
                        "school. Please check the staff ID and try again."
                    ),
                }
            )

    if errors:
        return StaffBulkProvisionResult(
            school_name=school.name,
            created=0,
            invitations=[],
            errors=errors,
        )

    # One random unusable hash is enough for the whole batch: every account
    # remains inactive until the invitee sets a real password.
    unusable_hash = await asyncio.to_thread(hash_password, secrets.token_urlsafe(64))
    created_users: list[tuple[int, User, StaffBulkImportRow]] = []

    try:
        for row_number, row, email, _ in normalized_rows:
            user = User(
                school_id=school_id,
                email=email,
                password_hash=unusable_hash,
                first_name=row.first_name,
                last_name=row.last_name,
                phone_number=row.phone_number,
                role=row.role,
                is_active=False,
            )
            db.add(user)
            created_users.append((row_number, user, row))

        await db.flush()

        for _, user, row in created_users:
            db.add(
                StaffProfile(
                    school_id=school_id,
                    user_id=user.id,
                    staff_id_number=row.staff_id_number,
                    employment_status="INVITED",
                    specialization=row.specialization,
                )
            )

        record_audit_log(
            db,
            AuditContext(school_id=school_id, actor_id=granter_id, actor_ip=actor_ip),
            action="STAFF_BATCH_PROVISIONED",
            resource_type="StaffBulkImport",
            resource_id=generate_uuid7(),
            diff_payload={"created": len(created_users)},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return StaffBulkProvisionResult(
            school_name=school.name,
            created=0,
            invitations=[],
            errors=[
                {
                    "row": 0,
                    "message": (
                        "We couldn't save this staff list because one or more "
                        "details conflict with existing records. Please review the "
                        "file and try again."
                    ),
                }
            ],
        )

    invitations = [_build_staff_invitation(user, school_id) for _, user, _ in created_users]
    return StaffBulkProvisionResult(
        school_name=school.name,
        created=len(invitations),
        invitations=invitations,
        errors=[],
    )


async def verify_staff_invitation(
    db: AsyncSession, invitation_token: str
) -> StaffVerifyInviteResponse:
    try:
        payload = decode_token(invitation_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invitation link is invalid or has expired. Please ask your school administrator to send a new invitation.",
        ) from None

    if payload.get("type") != "invitation":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided token is not a valid staff invitation token.",
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid invitation token payload.",
        )

    user_id = UUID(user_id_str)
    stmt = (
        select(User)
        .options(selectinload(User.school), selectinload(User.staff_profile))
        .where(User.id == user_id)
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The invited staff account could not be found.",
        )

    if user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invitation has already been accepted. You can log in directly.",
        )

    school_name = user.school.name if user.school else "Your School"
    return StaffVerifyInviteResponse(
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        school_name=school_name,
        role=user.role,
    )


async def accept_staff_invitation(
    db: AsyncSession, req: AcceptInvitationRequest, actor_ip: str = "0.0.0.0"
) -> tuple[User, TokenResponse]:
    try:
        payload = decode_token(req.invitation_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invitation link is invalid or has expired. Please ask your school administrator to send a new invitation.",
        ) from None

    if payload.get("type") != "invitation":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided token is not a valid staff invitation token.",
        )

    user_id = UUID(payload.get("sub"))
    stmt = (
        select(User)
        .options(selectinload(User.school), selectinload(User.staff_profile))
        .where(User.id == user_id)
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The invited staff account could not be found.",
        )

    if user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invitation has already been used. Please sign in with your email and password.",
        )

    user.password_hash = await asyncio.to_thread(hash_password, req.password)
    user.is_active = True
    user.token_valid_after = datetime.now(UTC)
    if user.staff_profile:
        user.staff_profile.employment_status = "ACTIVE"

    await db.flush()
    if user.school_id is not None:
        await _scope_audit_to_school(db, user.school_id)
        record_audit_log(
            db,
            AuditContext(school_id=user.school_id, actor_id=user.id, actor_ip=actor_ip),
            action="STAFF_INVITATION_ACCEPTED",
            resource_type="User",
            resource_id=user.id,
            diff_payload={"after": {"is_active": True}},
        )
    await db.commit()
    await db.refresh(user)

    tokens = await create_tokens_for_user(user)
    return user, tokens


async def grant_user_permission(
    db: AsyncSession,
    school_id: UUID,
    granter_id: UUID,
    target_user_id: UUID,
    req: PermissionGrantRequest,
    actor_ip: str = "0.0.0.0",
) -> UserPermissionGrant:
    # Verify target user belongs to school
    target_user = await db.get(User, target_user_id)
    if not target_user or target_user.school_id != school_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected staff member was not found in your school.",
        )

    # Find permission
    perm_res = await db.execute(
        select(Permission).where(Permission.code == req.permission_code.strip())
    )
    permission = perm_res.scalar_one_or_none()
    if not permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"The permission '{req.permission_code}' is not recognized in the system.",
        )

    # Check for existing grant with same scope
    existing_grant_res = await db.execute(
        select(UserPermissionGrant).where(
            and_(
                UserPermissionGrant.user_id == target_user_id,
                UserPermissionGrant.permission_id == permission.id,
                UserPermissionGrant.scope_type == req.scope_type.value,
                UserPermissionGrant.department_id == req.department_id,
                UserPermissionGrant.academic_level_id == req.academic_level_id,
                UserPermissionGrant.class_group_id == req.class_group_id,
                UserPermissionGrant.deleted_at.is_(None),
            )
        )
    )
    if existing_grant_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This user already has this specific scoped permission.",
        )

    grant = UserPermissionGrant(
        school_id=school_id,
        user_id=target_user_id,
        permission_id=permission.id,
        granted_by_id=granter_id,
        scope_type=req.scope_type.value,
        department_id=req.department_id,
        academic_level_id=req.academic_level_id,
        class_group_id=req.class_group_id,
    )
    db.add(grant)
    await db.flush()
    record_audit_log(
        db,
        AuditContext(school_id=school_id, actor_id=granter_id, actor_ip=actor_ip),
        action="PERMISSION_GRANTED",
        resource_type="UserPermissionGrant",
        resource_id=grant.id,
        diff_payload={
            "after": {
                "user_id": target_user_id,
                "permission_code": permission.code,
                "scope_type": req.scope_type.value,
            }
        },
    )
    await _commit_or_conflict(
        db,
        message="This user already has this specific scoped permission.",
        code="PERMISSION_GRANT_CONFLICT",
    )
    await db.refresh(grant)
    return grant


async def revoke_user_permission(
    db: AsyncSession,
    school_id: UUID,
    target_user_id: UUID,
    grant_id: UUID,
    actor_id: UUID,
    actor_ip: str = "0.0.0.0",
) -> None:
    stmt = select(UserPermissionGrant).where(
        and_(
            UserPermissionGrant.id == grant_id,
            UserPermissionGrant.user_id == target_user_id,
            UserPermissionGrant.school_id == school_id,
            UserPermissionGrant.deleted_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    grant = result.scalar_one_or_none()
    if not grant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The permission grant you are trying to revoke does not exist.",
        )

    grant.deleted_at = datetime.now(UTC)
    await db.flush()
    record_audit_log(
        db,
        AuditContext(school_id=school_id, actor_id=actor_id, actor_ip=actor_ip),
        action="PERMISSION_REVOKED",
        resource_type="UserPermissionGrant",
        resource_id=grant.id,
        diff_payload={"before": {"deleted_at": None}, "after": {"deleted_at": grant.deleted_at}},
    )
    await db.commit()


async def deactivate_user(
    db: AsyncSession,
    school_id: UUID,
    target_user_id: UUID,
    actor_id: UUID,
    actor_ip: str = "0.0.0.0",
) -> User:
    stmt = (
        select(User)
        .options(selectinload(User.staff_profile))
        .where(and_(User.id == target_user_id, User.school_id == school_id))
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The specified user could not be found in your school.",
        )

    user.is_active = False
    user.token_valid_after = datetime.now(UTC)
    if user.staff_profile:
        user.staff_profile.employment_status = "INACTIVE"

    from app.modules.academic_structure.models import ClassGroup
    from app.modules.subjects.models import TeacherAssignment

    active_assignments = await db.execute(
        select(TeacherAssignment).where(
            and_(
                TeacherAssignment.teacher_id == user.id,
                TeacherAssignment.school_id == school_id,
                TeacherAssignment.is_active.is_(True),
            )
        )
    )
    deactivated_assignment_ids = []
    for assignment in active_assignments.scalars().all():
        assignment.is_active = False
        assignment.end_date = date.today()
        deactivated_assignment_ids.append(str(assignment.id))

    form_master_groups = await db.execute(
        select(ClassGroup).where(
            and_(
                ClassGroup.school_id == school_id,
                ClassGroup.class_teacher_id == user.id,
                ClassGroup.deleted_at.is_(None),
            )
        )
    )
    cleared_class_group_ids = []
    for class_group in form_master_groups.scalars().all():
        class_group.class_teacher_id = None
        cleared_class_group_ids.append(str(class_group.id))

    await db.flush()
    record_audit_log(
        db,
        AuditContext(school_id=school_id, actor_id=actor_id, actor_ip=actor_ip),
        action="USER_DEACTIVATED",
        resource_type="User",
        resource_id=user.id,
        diff_payload={
            "after": {"is_active": False},
            "deactivated_teacher_assignment_ids": deactivated_assignment_ids,
            "cleared_form_master_class_group_ids": cleared_class_group_ids,
        },
    )
    await db.commit()
    await db.refresh(user)
    return user


async def get_user_effective_permissions(db: AsyncSession, user: User) -> list[str]:
    """Returns the set of permission codes effective for the user."""
    from app.modules.identity.models import RoleDefaultPermission

    if user.role in ("SUPER_ADMIN", "SCHOOL_ADMIN"):
        all_perms = await db.execute(select(Permission.code))
        return list(all_perms.scalars().all())

    role_perms_stmt = (
        select(Permission.code)
        .join(RoleDefaultPermission, RoleDefaultPermission.permission_id == Permission.id)
        .where(RoleDefaultPermission.role == user.role)
    )
    role_perms = (await db.execute(role_perms_stmt)).scalars().all()

    user_grants_stmt = (
        select(Permission.code)
        .join(UserPermissionGrant, UserPermissionGrant.permission_id == Permission.id)
        .where(
            UserPermissionGrant.user_id == user.id,
            UserPermissionGrant.deleted_at.is_(None),
        )
    )
    grant_perms = (await db.execute(user_grants_stmt)).scalars().all()

    return sorted(list(set(role_perms).union(set(grant_perms))))


async def list_school_users(
    db: AsyncSession,
    school_id: UUID,
    *,
    q: str | None = None,
    role: str | None = None,
    employment_status: str | None = None,
    specialization: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], PageMeta, dict[str, int]]:
    from sqlalchemy import case

    from app.modules.common.schemas import PageMeta
    from app.modules.identity.models import StaffProfile

    base_filter = (User.school_id == school_id, User.deleted_at.is_(None))
    summary_row = (
        await db.execute(
            select(
                func.count(User.id),
                func.coalesce(
                    func.sum(
                        case((and_(User.role == "TEACHER", User.is_active.is_(True)), 1), else_=0)
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(case((StaffProfile.employment_status == "INVITED", 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                or_(
                                    User.is_active.is_(False),
                                    StaffProfile.employment_status == "INACTIVE",
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .select_from(User)
            .outerjoin(StaffProfile, StaffProfile.user_id == User.id)
            .where(*base_filter)
        )
    ).one()
    summary = {
        "total": int(summary_row[0] or 0),
        "teachers": int(summary_row[1] or 0),
        "pending_invites": int(summary_row[2] or 0),
        "deactivated": int(summary_row[3] or 0),
    }

    stmt = (
        select(User, StaffProfile)
        .outerjoin(StaffProfile, StaffProfile.user_id == User.id)
        .where(*base_filter)
    )
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                User.first_name.ilike(pattern),
                User.last_name.ilike(pattern),
                User.email.ilike(pattern),
                StaffProfile.staff_id_number.ilike(pattern),
            )
        )
    if role:
        stmt = stmt.where(User.role == role.strip().upper())
    if employment_status:
        status_val = employment_status.strip().upper()
        if status_val == "INACTIVE":
            stmt = stmt.where(
                or_(User.is_active.is_(False), StaffProfile.employment_status == "INACTIVE")
            )
        elif status_val == "ACTIVE":
            stmt = stmt.where(
                User.is_active.is_(True),
                or_(StaffProfile.id.is_(None), StaffProfile.employment_status == "ACTIVE"),
            )
        else:
            stmt = stmt.where(StaffProfile.employment_status == status_val)
    if specialization:
        stmt = stmt.where(StaffProfile.specialization.ilike(f"%{specialization.strip()}%"))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    rows = (
        await db.execute(stmt.order_by(User.created_at.desc()).offset(offset).limit(page_size))
    ).all()

    items = [
        {
            "id": u.id,
            "email": u.email,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "phone_number": u.phone_number,
            "role": u.role,
            "is_active": u.is_active,
            "employment_status": sp.employment_status
            if sp
            else ("ACTIVE" if u.is_active else "INACTIVE"),
            "staff_id_number": sp.staff_id_number if sp else None,
            "specialization": sp.specialization if sp else None,
            "last_login_at": u.last_login_at,
            "created_at": u.created_at,
        }
        for u, sp in rows
    ]
    meta = PageMeta.build(page=page, page_size=page_size, total=total)
    return items, meta, summary


async def list_permissions_catalog(db: AsyncSession) -> list[Permission]:
    result = await db.execute(select(Permission).order_by(Permission.category, Permission.code))
    return list(result.scalars().all())


async def list_user_permission_grants(
    db: AsyncSession, school_id: UUID, user_id: UUID
) -> list[dict[str, Any]]:
    user = (
        await db.execute(
            select(User).where(
                User.id == user_id, User.school_id == school_id, User.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if user is None:
        raise ResourceNotFoundException("staff member", user_id)

    rows = (
        await db.execute(
            select(UserPermissionGrant, Permission)
            .join(Permission, Permission.id == UserPermissionGrant.permission_id)
            .where(
                UserPermissionGrant.school_id == school_id,
                UserPermissionGrant.user_id == user_id,
                UserPermissionGrant.deleted_at.is_(None),
            )
            .order_by(UserPermissionGrant.created_at.desc())
        )
    ).all()

    return [
        {
            "id": grant.id,
            "permission_id": perm.id,
            "permission_code": perm.code,
            "permission_name": perm.name,
            "scope_type": grant.scope_type,
            "department_id": grant.department_id,
            "academic_level_id": grant.academic_level_id,
            "class_group_id": grant.class_group_id,
            "created_at": grant.created_at,
        }
        for grant, perm in rows
    ]


async def update_my_profile(
    db: AsyncSession,
    user: User,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    phone_number: str | None = None,
) -> User:
    db_user = (
        await db.execute(
            select(User)
            .options(selectinload(User.school), selectinload(User.staff_profile))
            .where(User.id == user.id)
        )
    ).scalar_one()
    if first_name is not None:
        db_user.first_name = first_name.strip()
    if last_name is not None:
        db_user.last_name = last_name.strip()
    if phone_number is not None:
        db_user.phone_number = phone_number.strip() or None
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def get_assigned_class_groups(
    db: AsyncSession, school_id: UUID, user_id: UUID
) -> list[ClassGroup]:
    active_session_id = (
        await db.execute(
            select(AcademicSession.id)
            .where(
                AcademicSession.school_id == school_id,
                AcademicSession.is_current.is_(True),
                AcademicSession.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_session_id is None:
        return []
    return list(
        (
            await db.execute(
                select(ClassGroup)
                .where(
                    ClassGroup.school_id == school_id,
                    ClassGroup.academic_session_id == active_session_id,
                    ClassGroup.class_teacher_id == user_id,
                    ClassGroup.deleted_at.is_(None),
                )
                .order_by(ClassGroup.display_name.asc())
            )
        )
        .scalars()
        .all()
    )


async def change_my_password(
    db: AsyncSession,
    user: User,
    *,
    current_password: str,
    new_password: str,
) -> None:
    db_user = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    valid = await asyncio.to_thread(verify_password, current_password, db_user.password_hash)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your current password is incorrect. Please check it and try again.",
        )
    db_user.password_hash = await asyncio.to_thread(hash_password, new_password)
    await db.commit()


async def update_school_user(
    db: AsyncSession,
    school_id: UUID,
    target_user_id: UUID,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    phone_number: str | None = None,
    role: str | None = None,
    staff_id_number: str | None = None,
    specialization: str | None = None,
    actor_id: UUID,
    actor_ip: str,
) -> dict[str, Any]:
    target = (
        await db.execute(
            select(User)
            .options(selectinload(User.staff_profile))
            .where(
                User.id == target_user_id,
                User.school_id == school_id,
                User.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise ResourceNotFoundException("staff member", target_user_id)

    if first_name is not None:
        target.first_name = first_name
    if last_name is not None:
        target.last_name = last_name
    if phone_number is not None:
        target.phone_number = phone_number.strip() or None
    if role is not None:
        target.role = role

    sp = target.staff_profile
    if sp is not None:
        if staff_id_number is not None:
            sp.staff_id_number = staff_id_number
        if specialization is not None:
            sp.specialization = specialization.strip() or None

    await db.flush()
    record_audit_log(
        db,
        AuditContext(school_id=school_id, actor_id=actor_id, actor_ip=actor_ip),
        action="STAFF_UPDATED",
        resource_type="User",
        resource_id=target.id,
        diff_payload={"role": target.role},
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictException(
            message="That staff ID number is already assigned to another staff member in your school.",
            code="STAFF_ID_CONFLICT",
        ) from exc

    await db.refresh(target)
    if sp is not None:
        await db.refresh(sp)
    return {
        "id": target.id,
        "email": target.email,
        "first_name": target.first_name,
        "last_name": target.last_name,
        "phone_number": target.phone_number,
        "role": target.role,
        "is_active": target.is_active,
        "employment_status": sp.employment_status
        if sp
        else ("ACTIVE" if target.is_active else "INACTIVE"),
        "staff_id_number": sp.staff_id_number if sp else None,
        "specialization": sp.specialization if sp else None,
        "last_login_at": target.last_login_at,
        "created_at": target.created_at,
    }
