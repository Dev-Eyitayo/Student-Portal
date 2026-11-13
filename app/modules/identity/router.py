import time
from typing import Dict, List, Optional
from uuid import UUID
from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import (
    get_current_authenticated_user,
    get_db,
    get_tenant_db,
    oauth2_scheme,
)
from app.core.permissions import require_permission
from app.core.security import decode_access_token, revoke_access_token
from app.modules.identity.models import User
from app.modules.identity.schemas import (
    AcceptInvitationRequest,
    DeactivateUserResponse,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    PermissionGrantRequest,
    RefreshTokenRequest,
    SchoolCreateRequest,
    SchoolResponse,
    StaffInviteRequest,
    StaffInviteResponse,
    StaffVerifyInviteResponse,
    TokenResponse,
    UserPermissionGrantResponse,
)
from app.modules.identity import services

router = APIRouter(prefix="/identity", tags=["Identity & Access Management"])

settings = get_settings()

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/identity"
REFRESH_COOKIE_SECURE = settings.ENVIRONMENT not in ("development", "testing", "test")


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Stores the refresh token in an HTTP-only cookie scoped to the identity API."""
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=REFRESH_COOKIE_SECURE,
        samesite="lax",
    )


def _remaining_ttl(exp: Optional[float], fallback_seconds: int) -> int:
    if exp is None:
        return fallback_seconds
    return max(1, int(exp - time.time()))



@router.post("/schools", response_model=SchoolResponse, status_code=status.HTTP_201_CREATED)
async def register_school(
    req: SchoolCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Onboards a new school tenant and establishes the initial school administrative user.
    """
    school, _ = await services.register_school_with_admin(db, req)
    return school


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticates user credentials and issues access and refresh tokens.
    Access tokens contain a unique UUIDv7 jti for instant revocation.
    The refresh token is also stored in an HTTP-only cookie.
    """
    user = await services.authenticate_user(db, req.email, req.password)
    tokens = await services.create_tokens_for_user(user)
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    req: RefreshTokenRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchanges a valid refresh token for a fresh access and refresh token pair.
    """
    tokens = await services.refresh_user_tokens(db, req.refresh_token)
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    response: Response,
    req: Optional[LogoutRequest] = Body(default=None),
    refresh_cookie: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_authenticated_user),
):
    """
    Revokes the current access token and the associated refresh token by adding
    their jti values to the revocation store for their remaining lifetimes.
    """
    try:
        payload = decode_access_token(token)
        jti = payload.get("jti")
        if jti:
            await revoke_access_token(
                jti,
                expires_in_seconds=_remaining_ttl(
                    payload.get("exp"), settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
                ),
            )
    except Exception:
        # Even if access token decoding fails, a clean sign-out response is returned.
        pass

    refresh_token = (req.refresh_token if req else None) or refresh_cookie
    if refresh_token:
        try:
            refresh_payload = decode_access_token(refresh_token)
            refresh_jti = refresh_payload.get("jti")
            if refresh_jti:
                await revoke_access_token(
                    refresh_jti,
                    expires_in_seconds=_remaining_ttl(
                        refresh_payload.get("exp"), settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
                    ),
                )
        except Exception:
            # A malformed or already-expired refresh token does not block sign-out.
            pass

    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    return {"message": "You have been successfully signed out."}


@router.get("/me", response_model=MeResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Returns the authenticated user's profile, linked school details, and effective permissions.
    """
    permissions = await services.get_user_effective_permissions(db, current_user)
    school_name = current_user.school.name if current_user.school else None

    return MeResponse(
        id=current_user.id,
        school_id=current_user.school_id,
        school_name=school_name,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone_number=current_user.phone_number,
        role=current_user.role,
        is_active=current_user.is_active,
        is_mfa_enabled=current_user.is_mfa_enabled,
        staff_profile=current_user.staff_profile,
        permissions=permissions,
    )


@router.post(
    "/staff/invite",
    response_model=StaffInviteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("users:manage"))],
)
async def invite_staff(
    req: StaffInviteRequest,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Generates a secure, 72-hour invitation token for onboarding a new teacher or staff member.
    """
    if not current_user.school_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Staff invitation requires an active school context.",
        )
    return await services.invite_staff_member(db, current_user.school_id, current_user.id, req)


@router.get("/staff/verify-invite", response_model=StaffVerifyInviteResponse)
async def verify_invite(
    token: str = Query(..., description="The 72-hour invitation token received by the teacher"),
    db: AsyncSession = Depends(get_db),
):
    """
    Verifies an invitation token and returns teacher details to the onboarding page.
    """
    return await services.verify_staff_invitation(db, token)


@router.post("/staff/accept-invitation", response_model=TokenResponse)
async def accept_invitation(
    req: AcceptInvitationRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Completes teacher onboarding by setting their password, activating the account,
    and returning initial session tokens.
    """
    _, tokens = await services.accept_staff_invitation(db, req)
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens


@router.post(
    "/users/{user_id}/permissions",
    response_model=UserPermissionGrantResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("users:manage"))],
)
async def grant_permission(
    user_id: UUID,
    req: PermissionGrantRequest,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Grants a scoped permission to a user (e.g. HOD for Science, Form Master for SS1 O).
    """
    if not current_user.school_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permission delegation requires an active school context.",
        )
    grant = await services.grant_user_permission(
        db=db,
        school_id=current_user.school_id,
        granter_id=current_user.id,
        target_user_id=user_id,
        req=req,
    )
    return grant


@router.delete(
    "/users/{user_id}/permissions/{grant_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("users:manage"))],
)
async def revoke_permission(
    user_id: UUID,
    grant_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Revokes a previously assigned scoped permission grant.
    """
    if not current_user.school_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permission management requires an active school context.",
        )
    await services.revoke_user_permission(
        db=db,
        school_id=current_user.school_id,
        target_user_id=user_id,
        grant_id=grant_id,
    )
    return {"message": "The permission grant has been successfully revoked."}


@router.post(
    "/users/{user_id}/deactivate",
    response_model=DeactivateUserResponse,
    dependencies=[Depends(require_permission("users:manage"))],
)
async def deactivate_user(
    user_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Deactivates a user account, marks staff profile INACTIVE, and advances token_valid_after
    to invalidate all active sessions immediately.
    """
    if not current_user.school_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User management requires an active school context.",
        )
    user = await services.deactivate_user(db, current_user.school_id, user_id)
    return DeactivateUserResponse(
        user_id=user.id,
        is_active=user.is_active,
        message=f"The account for {user.first_name} {user.last_name} has been deactivated and signed out on all devices.",
    )
