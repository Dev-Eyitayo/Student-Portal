import base64
from pathlib import Path
from uuid import UUID

from celery.result import AsyncResult
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.dependencies import get_current_authenticated_user, get_tenant_db
from app.core.permissions import require_permission
from app.core.redis import get_redis
from app.modules.audit.services import AuditContext
from app.modules.common.models import generate_uuid7
from app.modules.common.schemas import Page, SuccessResponse
from app.modules.identity.models import User
from app.modules.students import attendance_services, services
from app.modules.students.bulk_import import MAX_STUDENT_IMPORT_BYTES
from app.modules.students.schemas import (
    AttendanceRecordResponse,
    AttendanceSummaryResponse,
    AttendanceSummaryUpdateRequest,
    EnrollmentAttendanceMarkRequest,
    EnrollmentCreateRequest,
    EnrollmentResponse,
    EnrollmentUpdateRequest,
    StudentBulkImportAcceptedResponse,
    StudentBulkImportStatusResponse,
    StudentCreateRequest,
    StudentDirectorySummaryResponse,
    StudentResponse,
    StudentStatusUpdateRequest,
    StudentUpdateRequest,
)
from app.modules.students.tasks import (
    STAGING_KEY,
    STAGING_TTL_SECONDS,
    process_student_bulk_import,
)

router = APIRouter(tags=["Students & Enrollments"])

student_permissions = [Depends(require_permission("students:register"))]
status_permissions = [Depends(require_permission("students:status"))]
bulk_import_permissions = [Depends(require_permission("students:bulk_import"))]


def _school_id(current_user: User) -> UUID:
    if current_user.school_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not linked to an active school. Please contact your school administrator.",
        )
    return current_user.school_id


def _audit_context(request: Request, current_user: User) -> AuditContext:
    client_ip = request.client.host if request.client else "0.0.0.0"
    return AuditContext(
        school_id=_school_id(current_user),
        actor_id=current_user.id,
        actor_ip=client_ip,
    )


@router.get(
    "/students/summary",
    response_model=SuccessResponse[StudentDirectorySummaryResponse],
    dependencies=student_permissions,
)
async def get_students_summary(
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    summary = await services.get_students_summary(db, _school_id(current_user))
    return SuccessResponse[StudentDirectorySummaryResponse](
        data=StudentDirectorySummaryResponse.model_validate(summary)
    )


@router.get(
    "/students",
    response_model=SuccessResponse[Page[StudentResponse]],
    dependencies=student_permissions,
)
async def list_students(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, max_length=128),
    student_status: str | None = Query(None, alias="status"),
    gender: str | None = Query(None, max_length=16),
    academic_level_id: UUID | None = Query(None),
    class_group_id: UUID | None = Query(None),
    include_archived: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_students(
        db,
        _school_id(current_user),
        page=page,
        page_size=page_size,
        q=q,
        status=student_status,
        include_archived=include_archived,
        gender=gender,
        academic_level_id=academic_level_id,
        class_group_id=class_group_id,
    )
    return SuccessResponse[Page[StudentResponse]](
        data=Page[StudentResponse](items=items, meta=meta)
    )


@router.post(
    "/students",
    response_model=SuccessResponse[StudentResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=student_permissions,
)
async def create_student(
    req: StudentCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    student = await services.create_student(
        db,
        _school_id(current_user),
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[StudentResponse](data=StudentResponse.model_validate(student))


@router.post(
    "/students/bulk-import",
    response_model=SuccessResponse[StudentBulkImportAcceptedResponse],
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=bulk_import_permissions,
)
async def bulk_import_students(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_authenticated_user),
):
    school_id = _school_id(current_user)
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please upload an Excel (.xlsx) or CSV (.csv) file.",
        )

    content = await file.read(MAX_STUDENT_IMPORT_BYTES + 1)
    if len(content) > MAX_STUDENT_IMPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail="This file is too large. Please upload a file smaller than 5 MB.",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This file is empty. Please add student rows and upload again.",
        )

    job_id = str(generate_uuid7())
    staging_key = STAGING_KEY.format(school_id=school_id, job_id=job_id)
    client_ip = request.client.host if request.client else "0.0.0.0"

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
            detail="We couldn't queue the student import right now. Please try again in a moment.",
        ) from None

    try:
        process_student_bulk_import.apply_async(
            args=[
                job_id,
                str(school_id),
                str(current_user.id),
                client_ip,
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
            detail="We couldn't start the student import right now. Please try again in a moment.",
        ) from None

    return SuccessResponse[StudentBulkImportAcceptedResponse](
        data=StudentBulkImportAcceptedResponse(
            task_id=job_id,
            message=(
                "Your student list has been queued for import. You can check its progress "
                "using the task ID."
            ),
        )
    )


@router.get(
    "/students/bulk-import/{task_id}",
    response_model=SuccessResponse[StudentBulkImportStatusResponse],
    dependencies=bulk_import_permissions,
)
async def get_student_bulk_import_status(
    task_id: str,
    current_user: User = Depends(get_current_authenticated_user),
):
    school_id = _school_id(current_user)

    async_result = AsyncResult(task_id, app=celery_app)
    state = async_result.state
    result: dict[str, object] | None = None

    if async_result.successful():
        raw_result = async_result.result
        if isinstance(raw_result, dict):
            if raw_result.get("school_id") != str(school_id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="This student import task could not be found.",
                )
            result = {key: value for key, value in raw_result.items() if key != "school_id"}
        else:
            result = {"status": "completed"}
    elif state == "FAILURE":
        result = {
            "status": "failed",
            "message": (
                "The student import could not be completed. Please try again or contact your "
                "platform administrator."
            ),
        }

    return SuccessResponse[StudentBulkImportStatusResponse](
        data=StudentBulkImportStatusResponse(
            task_id=task_id,
            state=state,
            result=result,
        )
    )


@router.get(
    "/students/{student_id}",
    response_model=SuccessResponse[StudentResponse],
    dependencies=student_permissions,
)
async def get_student(
    student_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    student = await services.get_student(db, _school_id(current_user), student_id)
    return SuccessResponse[StudentResponse](data=StudentResponse.model_validate(student))


@router.patch(
    "/students/{student_id}",
    response_model=SuccessResponse[StudentResponse],
    dependencies=student_permissions,
)
async def update_student(
    student_id: UUID,
    req: StudentUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    student = await services.update_student(
        db,
        _school_id(current_user),
        student_id,
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[StudentResponse](data=StudentResponse.model_validate(student))


@router.post(
    "/students/{student_id}/status",
    response_model=SuccessResponse[StudentResponse],
    dependencies=status_permissions,
)
async def update_student_status(
    student_id: UUID,
    req: StudentStatusUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    student = await services.update_student_status(
        db,
        _school_id(current_user),
        student_id,
        req,
        _audit_context(request, current_user),
    )
    return SuccessResponse[StudentResponse](data=StudentResponse.model_validate(student))


@router.get(
    "/students/{student_id}/enrollments",
    response_model=SuccessResponse[Page[EnrollmentResponse]],
)
async def list_student_enrollments(
    student_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_student_enrollments(
        db,
        _school_id(current_user),
        student_id,
        current_user,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse[Page[EnrollmentResponse]](
        data=Page[EnrollmentResponse](items=items, meta=meta)
    )


@router.post(
    "/students/{student_id}/enrollments",
    response_model=SuccessResponse[EnrollmentResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_enrollment(
    student_id: UUID,
    req: EnrollmentCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    # Authorization is resource-scoped in services.create_enrollment, not a router dependency.
    enrollment = await services.create_enrollment(
        db,
        _school_id(current_user),
        student_id,
        req,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[EnrollmentResponse](data=EnrollmentResponse.model_validate(enrollment))


@router.get(
    "/enrollments/{enrollment_id}",
    response_model=SuccessResponse[EnrollmentResponse],
)
async def get_enrollment(
    enrollment_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    enrollment = await services.get_enrollment(
        db,
        _school_id(current_user),
        enrollment_id,
        current_user,
    )
    return SuccessResponse[EnrollmentResponse](data=EnrollmentResponse.model_validate(enrollment))


@router.patch(
    "/enrollments/{enrollment_id}",
    response_model=SuccessResponse[EnrollmentResponse],
)
async def update_enrollment(
    enrollment_id: UUID,
    req: EnrollmentUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    enrollment = await services.update_enrollment(
        db,
        _school_id(current_user),
        enrollment_id,
        req,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[EnrollmentResponse](data=EnrollmentResponse.model_validate(enrollment))


@router.post(
    "/enrollments/{enrollment_id}/attendance",
    response_model=SuccessResponse[AttendanceRecordResponse],
)
async def mark_enrollment_attendance(
    enrollment_id: UUID,
    req: EnrollmentAttendanceMarkRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    record = await attendance_services.mark_enrollment_attendance(
        db,
        _school_id(current_user),
        enrollment_id,
        req,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[AttendanceRecordResponse](
        data=AttendanceRecordResponse.model_validate(record)
    )


@router.get(
    "/enrollments/{enrollment_id}/attendance",
    response_model=SuccessResponse[Page[AttendanceRecordResponse]],
)
async def list_enrollment_attendance(
    enrollment_id: UUID,
    academic_term_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await attendance_services.list_enrollment_attendance(
        db,
        _school_id(current_user),
        enrollment_id,
        current_user,
        academic_term_id=academic_term_id,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse[Page[AttendanceRecordResponse]](
        data=Page(
            items=[AttendanceRecordResponse.model_validate(item) for item in items],
            meta=meta,
        )
    )


@router.get(
    "/enrollments/{enrollment_id}/attendance-summary",
    response_model=SuccessResponse[AttendanceSummaryResponse],
)
async def get_enrollment_attendance_summary(
    enrollment_id: UUID,
    academic_term_id: UUID = Query(...),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    summary = await attendance_services.get_enrollment_attendance_summary(
        db,
        _school_id(current_user),
        enrollment_id,
        academic_term_id,
        current_user,
    )
    return SuccessResponse[AttendanceSummaryResponse](
        data=AttendanceSummaryResponse.model_validate(summary)
    )


@router.patch(
    "/enrollments/{enrollment_id}/attendance-summary",
    response_model=SuccessResponse[AttendanceSummaryResponse],
)
async def update_enrollment_attendance_summary(
    enrollment_id: UUID,
    req: AttendanceSummaryUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    summary = await attendance_services.update_enrollment_attendance_summary(
        db,
        _school_id(current_user),
        enrollment_id,
        req,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[AttendanceSummaryResponse](
        data=AttendanceSummaryResponse.model_validate(summary)
    )
