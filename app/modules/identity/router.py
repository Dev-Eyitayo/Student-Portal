import base64
import time
from pathlib import Path
from uuid import UUID

from celery.result import AsyncResult
from fastapi import (
    APIRouter,
    Body,
    Cookie,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.dependencies import (
    get_current_authenticated_user,
    get_db,
    get_tenant_db,
    oauth2_scheme,
)
from app.core.permissions import require_permission
from app.core.redis import get_redis
from app.core.security import decode_access_token, revoke_access_token
from app.modules.common.models import generate_uuid7
from app.modules.common.schemas import MessageResponse, SuccessResponse
from app.modules.identity import services
from app.modules.identity.bulk_import import MAX_STAFF_IMPORT_BYTES
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
    StaffBulkImportAcceptedResponse,
    StaffBulkImportStatusResponse,
    StaffInviteRequest,
    StaffInviteResponse,
    StaffVerifyInviteResponse,
    TokenResponse,
    UserPermissionGrantResponse,
)
from app.modules.identity.tasks import (
    STAGING_KEY,
    STAGING_TTL_SECONDS,
    process_staff_bulk_import,
)

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


def _remaining_ttl(exp: float | None, fallback_seconds: int) -> int:
    if exp is None:
        return fallback_seconds
    return max(1, int(exp - time.time()))


@router.post(
    "/schools",
    response_model=SuccessResponse[SchoolResponse],
    status_code=status.HTTP_201_CREATED,
)
async def register_school(
    req: SchoolCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Onboards a new school tenant and establishes the initial school administrative user.
    """
    school, _ = await services.register_school_with_admin(db, req)
    return SuccessResponse[SchoolResponse](data=SchoolResponse.model_validate(school))


@router.post("/login", response_model=SuccessResponse[TokenResponse])
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
    return SuccessResponse[TokenResponse](data=tokens)


@router.post("/refresh", response_model=SuccessResponse[TokenResponse])
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
    return SuccessResponse[TokenResponse](data=tokens)


@router.post(
    "/logout",
    response_model=SuccessResponse[MessageResponse],
    status_code=status.HTTP_200_OK,
)
async def logout(
    response: Response,
    req: LogoutRequest | None = Body(default=None),
    refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
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
                        refresh_payload.get("exp"),
                        settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
                    ),
                )
        except Exception:
            # A malformed or already-expired refresh token does not block sign-out.
            pass

    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    return SuccessResponse[MessageResponse](
        data=MessageResponse(message="You have been successfully signed out.")
    )


@router.get("/me", response_model=SuccessResponse[MeResponse])
async def get_current_user_profile(
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Returns the authenticated user's profile, linked school details, and effective permissions.
    """
    permissions = await services.get_user_effective_permissions(db, current_user)
    school_name = current_user.school.name if current_user.school else None

    return SuccessResponse[MeResponse](
        data=MeResponse(
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
    )


@router.post(
    "/staff/invite",
    response_model=SuccessResponse[StaffInviteResponse],
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
    invitation = await services.invite_staff_member(
        db, current_user.school_id, current_user.id, req
    )
    return SuccessResponse[StaffInviteResponse](data=invitation)


@router.get("/staff/verify-invite", response_model=SuccessResponse[StaffVerifyInviteResponse])
async def verify_invite(
    token: str = Query(..., description="The 72-hour invitation token received by the teacher"),
    db: AsyncSession = Depends(get_db),
):
    """
    Verifies an invitation token and returns teacher details to the onboarding page.
    """
    invitation = await services.verify_staff_invitation(db, token)
    return SuccessResponse[StaffVerifyInviteResponse](data=invitation)


@router.post("/staff/accept-invitation", response_model=SuccessResponse[TokenResponse])
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
    return SuccessResponse[TokenResponse](data=tokens)


@router.post(
    "/staff/bulk-import",
    response_model=SuccessResponse[StaffBulkImportAcceptedResponse],
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("users:manage"))],
)
async def bulk_import_staff(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_authenticated_user),
):
    """
    Queues a CSV or Excel staff roster for asynchronous provisioning.
    """
    if not current_user.school_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Staff import requires an active school context.",
        )

    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please upload an Excel (.xlsx) or CSV (.csv) file.",
        )

    content = await file.read(MAX_STAFF_IMPORT_BYTES + 1)
    if len(content) > MAX_STAFF_IMPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail="This file is too large. Please upload a file smaller than 5 MB.",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This file is empty. Please add staff rows and upload again.",
        )

    job_id = str(generate_uuid7())
    staging_key = STAGING_KEY.format(school_id=current_user.school_id, job_id=job_id)

    try:
        client = get_redis()
        await client.set(
            staging_key,
            base64.b64encode(content).decode("ascii"),
            ex=STAGING_TTL_SECONDS,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("We couldn't queue the staff import right now. Please try again in a moment."),
        ) from None

    try:
        process_staff_bulk_import.apply_async(
            args=[
                job_id,
                str(current_user.school_id),
                str(current_user.id),
                filename,
            ],
            task_id=job_id,
            queue="bulk_imports",
        )
    except Exception:
        try:
            await client.delete(staging_key)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("We couldn't start the staff import right now. Please try again in a moment."),
        ) from None

    return SuccessResponse[StaffBulkImportAcceptedResponse](
        data=StaffBulkImportAcceptedResponse(
            task_id=job_id,
            message=(
                "Your staff list has been queued for import. You can check its "
                "progress using the task ID."
            ),
        )
    )


@router.get(
    "/staff/bulk-import/{task_id}",
    response_model=SuccessResponse[StaffBulkImportStatusResponse],
    dependencies=[Depends(require_permission("users:manage"))],
)
async def get_bulk_import_status(
    task_id: str,
    current_user: User = Depends(get_current_authenticated_user),
):
    if not current_user.school_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Staff import status requires an active school context.",
        )

    async_result = AsyncResult(task_id, app=celery_app)
    state = async_result.state
    result: dict[str, object] | None = None

    if async_result.successful():
        raw_result = async_result.result
        if isinstance(raw_result, dict):
            if raw_result.get("school_id") != str(current_user.school_id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="This staff import task could not be found.",
                )
            result = {key: value for key, value in raw_result.items() if key != "school_id"}
        else:
            result = {"status": "completed"}
    elif state == "FAILURE":
        result = {
            "status": "failed",
            "message": (
                "The staff import could not be completed. Please try again or "
                "contact your platform administrator."
            ),
        }

    return SuccessResponse[StaffBulkImportStatusResponse](
        data=StaffBulkImportStatusResponse(
            task_id=task_id,
            state=state,
            result=result,
        )
    )


@router.post(
    "/users/{user_id}/permissions",
    response_model=SuccessResponse[UserPermissionGrantResponse],
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
    return SuccessResponse[UserPermissionGrantResponse](data=grant)


@router.delete(
    "/users/{user_id}/permissions/{grant_id}",
    response_model=SuccessResponse[MessageResponse],
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
    return SuccessResponse[MessageResponse](
        data=MessageResponse(message="The permission grant has been successfully revoked.")
    )


@router.post(
    "/users/{user_id}/deactivate",
    response_model=SuccessResponse[DeactivateUserResponse],
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
    return SuccessResponse[DeactivateUserResponse](
        data=DeactivateUserResponse(
            user_id=user.id,
            is_active=user.is_active,
            message=(
                f"The account for {user.first_name} {user.last_name} has been "
                "deactivated and signed out on all devices."
            ),
        )
    )
