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
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.dependencies import (
    build_audit_context,
    get_current_authenticated_user,
    get_db,
    get_tenant_db,
    oauth2_scheme,
    require_school_id,
)
from app.core.permissions import require_permission
from app.core.redis import get_redis
from app.core.security import decode_access_token, revoke_access_token
from app.core.storage import storage_service
from app.modules.audit.services import AuditContext
from app.modules.common.models import generate_uuid7
from app.modules.common.schemas import MessageResponse, SuccessResponse
from app.modules.identity import services
from app.modules.identity.bulk_import import MAX_STAFF_IMPORT_BYTES
from app.modules.identity.models import User
from app.modules.identity.schemas import (
    AcceptInvitationRequest,
    AssignedClassGroupSummary,
    BrandingAssetType,
    BrandingUploadResponse,
    ChangePasswordRequest,
    DeactivateUserResponse,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    MeUpdateRequest,
    OnboardingTemplateResponse,
    PermissionCatalogItemResponse,
    PermissionGrantRequest,
    RefreshTokenRequest,
    SchoolConfigurationResponse,
    SchoolConfigurationUpdateRequest,
    SchoolCreateRequest,
    SchoolResponse,
    SchoolSetupResponse,
    SchoolSetupUpdateRequest,
    StaffBulkImportAcceptedResponse,
    StaffBulkImportStatusResponse,
    StaffDirectoryPageResponse,
    StaffDirectorySummary,
    StaffInviteRequest,
    StaffInviteResponse,
    StaffListItemResponse,
    StaffUpdateRequest,
    StaffVerifyInviteResponse,
    TokenResponse,
    UserPermissionGrantDetailResponse,
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


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "0.0.0.0"


@router.post(
    "/schools",
    response_model=SuccessResponse[SchoolResponse],
    status_code=status.HTTP_201_CREATED,
)
async def register_school(
    req: SchoolCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Onboards a new school tenant and establishes the initial school administrative user.
    """
    school, _ = await services.register_school_with_admin(db, req, _client_ip(request))
    return SuccessResponse[SchoolResponse](data=SchoolResponse.model_validate(school))


@router.get(
    "/onboarding/templates",
    response_model=SuccessResponse[list[OnboardingTemplateResponse]],
)
async def get_onboarding_templates():
    return SuccessResponse[list[OnboardingTemplateResponse]](
        data=services.get_onboarding_templates()
    )


@router.get(
    "/schools/{school_id}/setup",
    response_model=SuccessResponse[SchoolSetupResponse],
    dependencies=[Depends(require_permission("configuration:manage"))],
)
async def get_school_setup(
    school_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    if school_id != require_school_id(current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="We couldn't find this school's setup details. Please check that you're signed in to the right school.",
        )
    return SuccessResponse[SchoolSetupResponse](data=await services.get_school_setup(db, school_id))


@router.patch(
    "/schools/{school_id}/setup",
    response_model=SuccessResponse[SchoolSetupResponse],
    dependencies=[Depends(require_permission("configuration:manage"))],
)
async def update_school_setup(
    school_id: UUID,
    req: SchoolSetupUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    if school_id != require_school_id(current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="We couldn't find this school's setup details. Please check that you're signed in to the right school.",
        )
    result = await services.update_school_setup(
        db, school_id, req, build_audit_context(request, current_user)
    )
    return SuccessResponse[SchoolSetupResponse](data=result)


@router.post(
    "/schools/{school_id}/branding/uploads",
    response_model=SuccessResponse[BrandingUploadResponse],
    dependencies=[Depends(require_permission("configuration:manage"))],
)
async def upload_school_branding_asset(
    school_id: UUID,
    asset_type: BrandingAssetType,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    if school_id != require_school_id(current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="We couldn't find this school's branding settings. Please check that you're signed in to the right school.",
        )
    content = await file.read()
    storage_path = await services.save_school_branding_asset(
        db,
        school_id,
        asset_type,
        file.content_type or "",
        content,
        build_audit_context(request, current_user),
    )
    return SuccessResponse[BrandingUploadResponse](
        data=BrandingUploadResponse(
            asset_type=asset_type,
            storage_path=storage_path,
            url=storage_service.public_url(storage_path) or storage_path,
        )
    )


@router.get(
    "/schools/{school_id}/configuration",
    response_model=SuccessResponse[SchoolConfigurationResponse],
    dependencies=[Depends(require_permission("configuration:manage"))],
)
async def get_configuration(
    school_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    if school_id != require_school_id(current_user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="We couldn't find this school configuration. Please check that you're signed in to the right school.",
        )
    config = await services.get_school_configuration(db, school_id)
    return SuccessResponse[SchoolConfigurationResponse](
        data=SchoolConfigurationResponse.model_validate(config)
    )


@router.patch(
    "/schools/{school_id}/configuration",
    response_model=SuccessResponse[SchoolConfigurationResponse],
    dependencies=[Depends(require_permission("configuration:manage"))],
)
async def update_configuration(
    school_id: UUID,
    req: SchoolConfigurationUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    resolved_school_id = require_school_id(current_user)
    if school_id != resolved_school_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="We couldn't find this school configuration. Please check that you're signed in to the right school.",
        )
    audit = AuditContext(
        school_id=resolved_school_id,
        actor_id=current_user.id,
        actor_ip=_client_ip(request),
    )
    config = await services.update_school_configuration(db, school_id, req, current_user, audit)
    return SuccessResponse[SchoolConfigurationResponse](
        data=SchoolConfigurationResponse.model_validate(config)
    )


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


async def _build_me_response(db: AsyncSession, current_user: User) -> MeResponse:
    permissions = await services.get_user_effective_permissions(db, current_user)
    school_name = current_user.school.name if current_user.school else None
    school_type = (
        current_user.school.school_type.value
        if current_user.school and hasattr(current_user.school.school_type, "value")
        else (current_user.school.school_type if current_user.school else None)
    )
    assigned_class_groups: list[AssignedClassGroupSummary] = []
    if current_user.school_id is not None:
        groups = await services.get_assigned_class_groups(
            db, current_user.school_id, current_user.id
        )
        assigned_class_groups = [
            AssignedClassGroupSummary.model_validate(group) for group in groups
        ]

    return MeResponse(
        id=current_user.id,
        school_id=current_user.school_id,
        school_name=school_name,
        school_type=school_type,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone_number=current_user.phone_number,
        role=current_user.role,
        is_active=current_user.is_active,
        is_mfa_enabled=current_user.is_mfa_enabled,
        staff_profile=current_user.staff_profile,
        permissions=permissions,
        assigned_class_groups=assigned_class_groups,
    )


@router.get("/me", response_model=SuccessResponse[MeResponse])
async def get_current_user_profile(
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    Returns the authenticated user's profile, linked school details, and effective permissions.
    """
    return SuccessResponse[MeResponse](data=await _build_me_response(db, current_user))


@router.patch("/me", response_model=SuccessResponse[MeResponse])
async def update_current_user_profile(
    req: MeUpdateRequest,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    updated = await services.update_my_profile(
        db,
        current_user,
        first_name=req.first_name,
        last_name=req.last_name,
        phone_number=req.phone_number,
    )
    return SuccessResponse[MeResponse](data=await _build_me_response(db, updated))


@router.post("/change-password", response_model=SuccessResponse[MessageResponse])
async def change_current_user_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.change_my_password(
        db,
        current_user,
        current_password=req.current_password,
        new_password=req.new_password,
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(message="Your password has been updated.")
    )


@router.post(
    "/staff/invite",
    response_model=SuccessResponse[StaffInviteResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("users:manage"))],
)
async def invite_staff(
    req: StaffInviteRequest,
    request: Request,
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
        db, current_user.school_id, current_user.id, req, _client_ip(request)
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
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Completes teacher onboarding by setting their password, activating the account,
    and returning initial session tokens.
    """
    _, tokens = await services.accept_staff_invitation(db, req, _client_ip(request))
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
    request: Request,
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
        actor_ip=_client_ip(request),
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
    request: Request,
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
        actor_id=current_user.id,
        actor_ip=_client_ip(request),
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
    request: Request,
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
    user = await services.deactivate_user(
        db, current_user.school_id, user_id, current_user.id, _client_ip(request)
    )
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


@router.get(
    "/users",
    response_model=SuccessResponse[StaffDirectoryPageResponse],
    dependencies=[Depends(require_permission("users:manage"))],
)
async def list_school_users(
    q: str | None = Query(None, max_length=128),
    role: str | None = Query(None, max_length=32),
    employment_status: str | None = Query(None, alias="status", max_length=32),
    specialization: str | None = Query(None, alias="department", max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta, summary = await services.list_school_users(
        db,
        require_school_id(current_user),
        q=q,
        role=role,
        employment_status=employment_status,
        specialization=specialization,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse[StaffDirectoryPageResponse](
        data=StaffDirectoryPageResponse(
            items=[StaffListItemResponse.model_validate(item) for item in items],
            meta=meta,
            summary=StaffDirectorySummary.model_validate(summary),
        )
    )


@router.patch(
    "/users/{user_id}",
    response_model=SuccessResponse[StaffListItemResponse],
    dependencies=[Depends(require_permission("users:manage"))],
)
async def update_school_user(
    user_id: UUID,
    req: StaffUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    updated = await services.update_school_user(
        db,
        require_school_id(current_user),
        user_id,
        first_name=req.first_name,
        last_name=req.last_name,
        phone_number=req.phone_number,
        role=req.role.value if req.role is not None else None,
        staff_id_number=req.staff_id_number,
        specialization=req.specialization,
        actor_id=current_user.id,
        actor_ip=_client_ip(request),
    )
    return SuccessResponse[StaffListItemResponse](
        data=StaffListItemResponse.model_validate(updated)
    )


@router.get(
    "/permissions",
    response_model=SuccessResponse[list[PermissionCatalogItemResponse]],
    dependencies=[Depends(require_permission("users:manage"))],
)
async def list_permissions_catalog(
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    perms = await services.list_permissions_catalog(db)
    return SuccessResponse[list[PermissionCatalogItemResponse]](
        data=[PermissionCatalogItemResponse.model_validate(p) for p in perms]
    )


@router.get(
    "/users/{user_id}/permissions",
    response_model=SuccessResponse[list[UserPermissionGrantDetailResponse]],
    dependencies=[Depends(require_permission("users:manage"))],
)
async def list_user_permission_grants(
    user_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    grants = await services.list_user_permission_grants(
        db, require_school_id(current_user), user_id
    )
    return SuccessResponse[list[UserPermissionGrantDetailResponse]](
        data=[UserPermissionGrantDetailResponse.model_validate(g) for g in grants]
    )
