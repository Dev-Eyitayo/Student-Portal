from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_authenticated_user, get_tenant_db
from app.core.permissions import require_permission
from app.modules.audit.services import AuditContext
from app.modules.common.schemas import MessageResponse, Page, SuccessResponse
from app.modules.identity.models import User
from app.modules.subjects import services
from app.modules.subjects.schemas import (
    ClassSubjectCreateRequest,
    ClassSubjectResponse,
    MyTeachingAssignmentResponse,
    OfferingCreateRequest,
    OfferingResponse,
    OfferingUpdateRequest,
    SubjectCreateRequest,
    SubjectRegistrationBulkCreateRequest,
    SubjectRegistrationBulkResponse,
    SubjectRegistrationBulkSkip,
    SubjectRegistrationCreateRequest,
    SubjectRegistrationResponse,
    SubjectResponse,
    SubjectUpdateRequest,
    TeacherAssignmentBulkCreateRequest,
    TeacherAssignmentBulkResponse,
    TeacherAssignmentCreateRequest,
    TeacherAssignmentResponse,
)

router = APIRouter(
    prefix="/subjects",
    tags=["Subjects"],
)

class_subjects_router = APIRouter(
    prefix="/class-subjects",
    tags=["Class Subjects & Teacher Assignments"],
)

enrollment_registrations_router = APIRouter(
    prefix="/enrollments",
    tags=["Student Subject Registrations"],
)


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


# --------------------------------------------------------------------------
# Subject catalog
# --------------------------------------------------------------------------


@router.get(
    "",
    response_model=SuccessResponse[Page[SubjectResponse]],
    dependencies=[Depends(require_permission("subjects:manage"))],
)
async def list_subjects(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_retired: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_subjects(
        db,
        _school_id(current_user),
        page=page,
        page_size=page_size,
        include_retired=include_retired,
    )
    return SuccessResponse[Page[SubjectResponse]](
        data=Page[SubjectResponse](items=items, meta=meta)
    )


@router.post(
    "",
    response_model=SuccessResponse[SubjectResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("subjects:manage"))],
)
async def create_subject(
    req: SubjectCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    subject = await services.create_subject(
        db, _school_id(current_user), req, _audit_context(request, current_user)
    )
    return SuccessResponse[SubjectResponse](data=SubjectResponse.model_validate(subject))


@router.get(
    "/{subject_id}",
    response_model=SuccessResponse[SubjectResponse],
    dependencies=[Depends(require_permission("subjects:manage"))],
)
async def get_subject(
    subject_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    subject = await services.get_subject(db, _school_id(current_user), subject_id)
    return SuccessResponse[SubjectResponse](data=SubjectResponse.model_validate(subject))


@router.patch(
    "/{subject_id}",
    response_model=SuccessResponse[SubjectResponse],
    dependencies=[Depends(require_permission("subjects:manage"))],
)
async def update_subject(
    subject_id: UUID,
    req: SubjectUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    subject = await services.update_subject(
        db, _school_id(current_user), subject_id, req, _audit_context(request, current_user)
    )
    return SuccessResponse[SubjectResponse](data=SubjectResponse.model_validate(subject))


@router.delete(
    "/{subject_id}",
    response_model=SuccessResponse[MessageResponse],
    dependencies=[Depends(require_permission("subjects:manage"))],
)
async def retire_subject(
    subject_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.retire_subject(
        db, _school_id(current_user), subject_id, _audit_context(request, current_user)
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(
            message="The subject has been retired. Existing offerings remain available."
        )
    )


# --------------------------------------------------------------------------
# Subject offerings
# --------------------------------------------------------------------------


@router.get(
    "/offerings",
    response_model=SuccessResponse[Page[OfferingResponse]],
)
async def list_offerings(
    subject_id: UUID | None = Query(None),
    academic_session_id: UUID | None = Query(None),
    academic_level_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_retired: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_offerings(
        db,
        _school_id(current_user),
        current_user,
        subject_id=subject_id,
        academic_session_id=academic_session_id,
        academic_level_id=academic_level_id,
        page=page,
        page_size=page_size,
        include_retired=include_retired,
    )
    return SuccessResponse[Page[OfferingResponse]](
        data=Page[OfferingResponse](items=items, meta=meta)
    )


@router.post(
    "/offerings",
    response_model=SuccessResponse[OfferingResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_offering(
    req: OfferingCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    # Authorization is resource-scoped in services.create_offering, not a router dependency.
    offering = await services.create_offering(
        db, _school_id(current_user), req, current_user, _audit_context(request, current_user)
    )
    return SuccessResponse[OfferingResponse](data=OfferingResponse.model_validate(offering))


@router.get("/offerings/{offering_id}", response_model=SuccessResponse[OfferingResponse])
async def get_offering(
    offering_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    offering = await services.get_offering(db, _school_id(current_user), offering_id, current_user)
    return SuccessResponse[OfferingResponse](data=OfferingResponse.model_validate(offering))


@router.patch("/offerings/{offering_id}", response_model=SuccessResponse[OfferingResponse])
async def update_offering(
    offering_id: UUID,
    req: OfferingUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    offering = await services.update_offering(
        db,
        _school_id(current_user),
        offering_id,
        req,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[OfferingResponse](data=OfferingResponse.model_validate(offering))


@router.delete("/offerings/{offering_id}", response_model=SuccessResponse[MessageResponse])
async def retire_offering(
    offering_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.retire_offering(
        db,
        _school_id(current_user),
        offering_id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(message="The subject offering has been retired.")
    )


# --------------------------------------------------------------------------
# Class subjects
# --------------------------------------------------------------------------


@class_subjects_router.get(
    "",
    response_model=SuccessResponse[Page[ClassSubjectResponse]],
)
async def list_class_subjects(
    class_group_id: UUID | None = Query(None),
    subject_offering_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_retired: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_class_subjects(
        db,
        _school_id(current_user),
        current_user,
        class_group_id=class_group_id,
        subject_offering_id=subject_offering_id,
        page=page,
        page_size=page_size,
        include_retired=include_retired,
    )
    return SuccessResponse[Page[ClassSubjectResponse]](
        data=Page[ClassSubjectResponse](items=items, meta=meta)
    )


@class_subjects_router.get(
    "/my-assignments",
    response_model=SuccessResponse[Page[MyTeachingAssignmentResponse]],
)
async def list_my_teaching_assignments(
    academic_session_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_my_teaching_assignments(
        db,
        _school_id(current_user),
        current_user,
        academic_session_id=academic_session_id,
        page=page,
        page_size=page_size,
    )
    return SuccessResponse[Page[MyTeachingAssignmentResponse]](
        data=Page[MyTeachingAssignmentResponse](items=items, meta=meta)
    )


@class_subjects_router.post(
    "",
    response_model=SuccessResponse[ClassSubjectResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_class_subject(
    req: ClassSubjectCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    class_subject = await services.create_class_subject(
        db, _school_id(current_user), req, current_user, _audit_context(request, current_user)
    )
    return SuccessResponse[ClassSubjectResponse](
        data=ClassSubjectResponse.model_validate(class_subject)
    )


@class_subjects_router.get(
    "/{class_subject_id}",
    response_model=SuccessResponse[ClassSubjectResponse],
)
async def get_class_subject(
    class_subject_id: UUID,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    class_subject = await services.get_class_subject(
        db, _school_id(current_user), class_subject_id, current_user
    )
    return SuccessResponse[ClassSubjectResponse](
        data=ClassSubjectResponse.model_validate(class_subject)
    )


@class_subjects_router.delete(
    "/{class_subject_id}",
    response_model=SuccessResponse[MessageResponse],
)
async def retire_class_subject(
    class_subject_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.retire_class_subject(
        db,
        _school_id(current_user),
        class_subject_id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(message="The class subject has been retired.")
    )


# --------------------------------------------------------------------------
# Teacher assignments
# --------------------------------------------------------------------------


@class_subjects_router.get(
    "/{class_subject_id}/teacher-assignments",
    response_model=SuccessResponse[Page[TeacherAssignmentResponse]],
)
async def list_teacher_assignments(
    class_subject_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_inactive: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_teacher_assignments(
        db,
        _school_id(current_user),
        class_subject_id,
        current_user,
        page=page,
        page_size=page_size,
        include_inactive=include_inactive,
    )
    return SuccessResponse[Page[TeacherAssignmentResponse]](
        data=Page[TeacherAssignmentResponse](items=items, meta=meta)
    )


@class_subjects_router.post(
    "/{class_subject_id}/teacher-assignments",
    response_model=SuccessResponse[TeacherAssignmentResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_teacher_assignment(
    class_subject_id: UUID,
    req: TeacherAssignmentCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    assignment = await services.create_teacher_assignment(
        db,
        _school_id(current_user),
        class_subject_id,
        req.teacher_id,
        current_user.id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[TeacherAssignmentResponse](
        data=TeacherAssignmentResponse.model_validate(assignment)
    )


@class_subjects_router.delete(
    "/{class_subject_id}/teacher-assignments/{assignment_id}",
    response_model=SuccessResponse[MessageResponse],
)
async def deactivate_teacher_assignment(
    class_subject_id: UUID,
    assignment_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.deactivate_teacher_assignment(
        db,
        _school_id(current_user),
        class_subject_id,
        assignment_id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(message="The teacher has been unassigned from this class subject.")
    )


@class_subjects_router.post(
    "/teacher-assignments/bulk",
    response_model=SuccessResponse[TeacherAssignmentBulkResponse],
    status_code=status.HTTP_201_CREATED,
)
async def bulk_assign_teacher(
    req: TeacherAssignmentBulkCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    assigned, skipped = await services.bulk_assign_teacher(
        db,
        _school_id(current_user),
        req,
        current_user.id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[TeacherAssignmentBulkResponse](
        data=TeacherAssignmentBulkResponse(
            assigned=[TeacherAssignmentResponse.model_validate(a) for a in assigned],
            skipped=skipped,
        )
    )


# --------------------------------------------------------------------------
# Student subject registrations
# --------------------------------------------------------------------------


@class_subjects_router.post(
    "/{class_subject_id}/subject-registrations/bulk",
    response_model=SuccessResponse[SubjectRegistrationBulkResponse],
    status_code=status.HTTP_201_CREATED,
)
async def bulk_register_subject(
    class_subject_id: UUID,
    req: SubjectRegistrationBulkCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    registered, skipped = await services.bulk_register_subject(
        db,
        _school_id(current_user),
        class_subject_id,
        req.student_enrollment_ids,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[SubjectRegistrationBulkResponse](
        data=SubjectRegistrationBulkResponse(
            registered=[SubjectRegistrationResponse.model_validate(r) for r in registered],
            skipped=[
                SubjectRegistrationBulkSkip(student_enrollment_id=eid, reason=reason)
                for eid, reason in skipped
            ],
        )
    )


@enrollment_registrations_router.get(
    "/{enrollment_id}/subject-registrations",
    response_model=SuccessResponse[Page[SubjectRegistrationResponse]],
)
async def list_subject_registrations(
    enrollment_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_inactive: bool = Query(False),
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, meta = await services.list_subject_registrations(
        db,
        _school_id(current_user),
        enrollment_id,
        current_user,
        page=page,
        page_size=page_size,
        include_inactive=include_inactive,
    )
    return SuccessResponse[Page[SubjectRegistrationResponse]](
        data=Page[SubjectRegistrationResponse](items=items, meta=meta)
    )


@enrollment_registrations_router.post(
    "/{enrollment_id}/subject-registrations",
    response_model=SuccessResponse[SubjectRegistrationResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_subject_registration(
    enrollment_id: UUID,
    req: SubjectRegistrationCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    registration = await services.create_subject_registration(
        db,
        _school_id(current_user),
        enrollment_id,
        req.class_subject_id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[SubjectRegistrationResponse](
        data=SubjectRegistrationResponse.model_validate(registration)
    )


@enrollment_registrations_router.delete(
    "/{enrollment_id}/subject-registrations/{registration_id}",
    response_model=SuccessResponse[MessageResponse],
)
async def deactivate_subject_registration(
    enrollment_id: UUID,
    registration_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_authenticated_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    await services.deactivate_subject_registration(
        db,
        _school_id(current_user),
        enrollment_id,
        registration_id,
        current_user,
        _audit_context(request, current_user),
    )
    return SuccessResponse[MessageResponse](
        data=MessageResponse(message="The student's subject registration has been removed.")
    )
