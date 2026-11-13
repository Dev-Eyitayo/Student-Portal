from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_token_revoked,
    verify_password,
)
from app.modules.identity.models import (
    School,
    SchoolConfiguration,
    User,
    StaffProfile,
    Permission,
    UserPermissionGrant,
)
from app.modules.identity.schemas import (
    AcceptInvitationRequest,
    PermissionGrantRequest,
    SchoolCreateRequest,
    StaffInviteRequest,
    StaffInviteResponse,
    StaffVerifyInviteResponse,
    TokenResponse,
)


DEFAULT_ASSESSMENT_WEIGHTS = {
    "CA1": {"name": "Continuous Assessment 1", "max_score": 20, "weight_percentage": 20},
    "CA2": {"name": "Continuous Assessment 2", "max_score": 20, "weight_percentage": 20},
    "EXAM": {"name": "Terminal Examination", "max_score": 60, "weight_percentage": 60},
}

DEFAULT_GRADING_SCALE = [
    {"grade": "A1", "min_score": 75, "max_score": 100, "remark": "Excellent"},
    {"grade": "B2", "min_score": 70, "max_score": 74.99, "remark": "Very Good"},
    {"grade": "B3", "min_score": 65, "max_score": 69.99, "remark": "Good"},
    {"grade": "C4", "min_score": 60, "max_score": 64.99, "remark": "Credit"},
    {"grade": "C5", "min_score": 55, "max_score": 59.99, "remark": "Credit"},
    {"grade": "C6", "min_score": 50, "max_score": 54.99, "remark": "Credit"},
    {"grade": "D7", "min_score": 45, "max_score": 49.99, "remark": "Pass"},
    {"grade": "E8", "min_score": 40, "max_score": 44.99, "remark": "Pass"},
    {"grade": "F9", "min_score": 0, "max_score": 39.99, "remark": "Fail"},
]

DEFAULT_TERM_DEFINITIONS = [
    {"number": 1, "name": "First Term"},
    {"number": 2, "name": "Second Term"},
    {"number": 3, "name": "Third Term"},
]


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    stmt = (
        select(User)
        .options(selectinload(User.staff_profile), selectinload(User.school))
        .where(User.email == email.lower().strip())
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
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

    user.last_login_at = datetime.now(timezone.utc)
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
        )

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
    token_iat = datetime.fromtimestamp(payload.get("iat", 0), tz=timezone.utc)
    if user.token_valid_after.replace(microsecond=0) > token_iat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has been invalidated because your security settings changed. Please sign in again.",
        )

    return await create_tokens_for_user(user)


async def register_school_with_admin(
    db: AsyncSession, req: SchoolCreateRequest
) -> Tuple[School, User]:
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

    # Create School
    school = School(
        slug=req.slug.lower().strip(),
        name=req.name.strip(),
        official_code=req.official_code.upper().strip(),
        country_code=req.country_code.upper().strip(),
        state=req.state.strip(),
        city=req.city.strip(),
        address=req.address.strip(),
        is_active=True,
    )
    db.add(school)
    await db.flush()

    # Create default configuration
    config = SchoolConfiguration(
        school_id=school.id,
        grading_scale_schema={"grades": DEFAULT_GRADING_SCALE},
        assessment_weight_schema=DEFAULT_ASSESSMENT_WEIGHTS,
        academic_term_definitions={"terms": DEFAULT_TERM_DEFINITIONS},
        timezone="Africa/Lagos",
        currency="NGN",
        report_card_template_id="default_v1",
    )
    db.add(config)

    # Create Initial Admin User
    admin_user = User(
        school_id=school.id,
        email=req.admin_email.lower().strip(),
        password_hash=hash_password(req.admin_password),
        first_name=req.admin_first_name.strip(),
        last_name=req.admin_last_name.strip(),
        role="SCHOOL_ADMIN",
        is_active=True,
    )
    db.add(admin_user)
    await db.commit()
    await db.refresh(school)
    await db.refresh(admin_user)

    return school, admin_user


async def invite_staff_member(
    db: AsyncSession, school_id: UUID, granter_id: UUID, req: StaffInviteRequest
) -> StaffInviteResponse:
    # Verify school exists
    school = await db.get(School, school_id)
    if not school:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The school associated with this request could not be found.",
        )

    # Check email uniqueness
    existing_user = await db.execute(
        select(User).where(User.email == req.email.lower().strip())
    )
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
    import secrets
    unusable_random_secret = secrets.token_urlsafe(64)
    temporary_hash = hash_password(unusable_random_secret)
    user = User(
        school_id=school_id,
        email=req.email.lower().strip(),
        password_hash=temporary_hash,
        first_name=req.first_name.strip(),
        last_name=req.last_name.strip(),
        phone_number=req.phone_number.strip() if req.phone_number else None,
        role=req.role,
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
    await db.commit()

    # Generate 72-hour invitation token
    expires_at = datetime.now(timezone.utc) + timedelta(hours=72)
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


async def verify_staff_invitation(db: AsyncSession, invitation_token: str) -> StaffVerifyInviteResponse:
    try:
        payload = decode_token(invitation_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invitation link is invalid or has expired. Please ask your school administrator to send a new invitation.",
        )

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
    db: AsyncSession, req: AcceptInvitationRequest
) -> Tuple[User, TokenResponse]:
    try:
        payload = decode_token(req.invitation_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invitation link is invalid or has expired. Please ask your school administrator to send a new invitation.",
        )

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

    user.password_hash = hash_password(req.password)
    user.is_active = True
    user.token_valid_after = datetime.now(timezone.utc)
    if user.staff_profile:
        user.staff_profile.employment_status = "ACTIVE"

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
                UserPermissionGrant.scope_type == req.scope_type,
                UserPermissionGrant.department_id == req.department_id,
                UserPermissionGrant.academic_level_id == req.academic_level_id,
                UserPermissionGrant.class_group_id == req.class_group_id,
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
        scope_type=req.scope_type,
        department_id=req.department_id,
        academic_level_id=req.academic_level_id,
        class_group_id=req.class_group_id,
    )
    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    return grant


async def revoke_user_permission(
    db: AsyncSession, school_id: UUID, target_user_id: UUID, grant_id: UUID
) -> None:
    stmt = select(UserPermissionGrant).where(
        and_(
            UserPermissionGrant.id == grant_id,
            UserPermissionGrant.user_id == target_user_id,
            UserPermissionGrant.school_id == school_id,
        )
    )
    result = await db.execute(stmt)
    grant = result.scalar_one_or_none()
    if not grant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The permission grant you are trying to revoke does not exist.",
        )

    await db.delete(grant)
    await db.commit()


async def deactivate_user(
    db: AsyncSession, school_id: UUID, target_user_id: UUID
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
    user.token_valid_after = datetime.now(timezone.utc)
    if user.staff_profile:
        user.staff_profile.employment_status = "INACTIVE"

    await db.commit()
    await db.refresh(user)
    return user


async def get_user_effective_permissions(db: AsyncSession, user: User) -> List[str]:
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
        .where(UserPermissionGrant.user_id == user.id)
    )
    grant_perms = (await db.execute(user_grants_stmt)).scalars().all()

    return sorted(list(set(role_perms).union(set(grant_perms))))

