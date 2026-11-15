import logging
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Human-readable translations for technical permission codes
PERMISSION_FRIENDLY_NAMES: dict[str, str] = {
    "scores:enter": "enter or edit scores",
    "scores:submit": "submit scores for review",
    "scores:approve": "approve scores",
    "scores:unlock": "unlock a locked score sheet",
    "students:enroll": "enroll students into classes",
    "students:register": "register new students",
    "students:bulk_import": "bulk import student records",
    "students:status": "update a student's status",
    "subjects:register_electives": "register elective or trade subjects",
    "subjects:manage": "manage subjects and subject offerings",
    "teachers:assign": "assign teachers to class subjects",
    "reports:publish": "publish academic term results",
    "classes:enter_remarks": "enter form master comments and conduct remarks",
    "structure:manage": "manage academic sessions, terms, and classes",
    "users:manage": "manage staff accounts",
    "permissions:delegate": "delegate permissions to staff",
    "audit:view": "view security and computation audit logs",
}

HTTP_ERROR_CODES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_409_CONFLICT: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_ERROR",
    status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
}


class DomainException(Exception):
    """Base domain exception with standardized, human-friendly error messages."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: list[dict[str, Any]] | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []
        super().__init__(message)


class TenantMismatchException(DomainException):
    def __init__(
        self,
        message: str = "We couldn't find this record in your school's account. Please check that you're signed in to the right school.",
    ):
        super().__init__(
            code="TENANT_MISMATCH", message=message, status_code=status.HTTP_403_FORBIDDEN
        )


class InsufficientPermissionException(DomainException):
    def __init__(self, permission_code: str, scope: str | None = None):
        action = PERMISSION_FRIENDLY_NAMES.get(
            permission_code, f"perform this action ({permission_code})"
        )
        if scope:
            msg = f"You don't have permission to {action} for {scope}. If you need this access, please ask your school administrator or academic officer."
        else:
            msg = f"You don't have permission to {action}. If you need this access, please ask your school administrator or academic officer."

        super().__init__(
            code="INSUFFICIENT_PERMISSIONS",
            message=msg,
            status_code=status.HTTP_403_FORBIDDEN,
            details=[{"permission_required": permission_code, "scope": scope}]
            if scope
            else [{"permission_required": permission_code}],
        )


class ResourceNotFoundException(DomainException):
    def __init__(self, resource: str, identifier: Any):
        super().__init__(
            code="RESOURCE_NOT_FOUND",
            message=f"We couldn't find the {resource.lower()} you were looking for. Please check the details and try again.",
            status_code=status.HTTP_404_NOT_FOUND,
            details=[{"resource": resource, "identifier": str(identifier)}],
        )


class ConflictException(DomainException):
    def __init__(
        self,
        message: str,
        code: str = "CONFLICT",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_409_CONFLICT,
            details=details,
        )


class ValidationException(DomainException):
    def __init__(self, message: str, details: list[dict[str, Any]] | None = None):
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            status_code=422,
            details=details,
        )


class ScoresIncompleteException(DomainException):
    def __init__(self, subject_name: str, missing_count: int):
        super().__init__(
            code="SCORES_INCOMPLETE",
            message=f"Scores cannot be submitted yet because {missing_count} student(s) in {subject_name} are still missing a score. Please enter their scores, or mark them as absent or exempt before submitting.",
            status_code=422,
            details=[{"subject": subject_name, "missing_students_count": missing_count}],
        )


async def domain_exception_handler(request: Request, exc: DomainException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "path": str(request.url.path),
            }
        },
    )


DEFAULT_HTTP_MESSAGES: dict[int, str] = {
    status.HTTP_401_UNAUTHORIZED: (
        "Please sign in to continue. If your session has expired, sign in again."
    ),
    status.HTTP_403_FORBIDDEN: (
        "You don't have permission to do that. If you need access, please ask your "
        "school administrator or academic officer."
    ),
    status.HTTP_404_NOT_FOUND: (
        "We couldn't find the page or record you requested. Please check the link and try again."
    ),
    status.HTTP_429_TOO_MANY_REQUESTS: (
        "Too many requests were sent in a short time. Please wait a moment and try again."
    ),
}


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Wraps FastAPI HTTP exceptions in the standard error envelope."""
    detail = exc.detail
    code = HTTP_ERROR_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
    details: list[dict[str, Any]] = []

    if isinstance(detail, dict):
        message = str(
            detail.get("message") or detail.get("detail") or "The request could not be completed."
        )
        code = str(detail.get("code") or code)
        raw_details = detail.get("details")
        if isinstance(raw_details, list):
            details = [item for item in raw_details if isinstance(item, dict)]
    else:
        message = str(detail) if detail else ""
        if message.lower() in {
            "not authenticated",
            "not found",
            "forbidden",
            "method not allowed",
            "internal server error",
        }:
            message = ""
        if not message:
            message = DEFAULT_HTTP_MESSAGES.get(
                exc.status_code, "The request could not be completed. Please try again."
            )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "path": str(request.url.path),
            }
        },
        headers=exc.headers,
    )


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Wraps request validation failures in the standard error envelope."""
    details: list[dict[str, Any]] = []
    for error in exc.errors():
        location = ".".join(
            str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path")
        )
        details.append(
            {
                "field": location or "request",
                "reason": str(error.get("msg", "Invalid value.")),
            }
        )

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": (
                    "Some of the details you entered need attention. "
                    "Please review the highlighted fields and try again."
                ),
                "details": details,
                "path": str(request.url.path),
            }
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Returns a safe, standard error envelope for unexpected failures."""
    logger.exception("Unhandled API exception", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": (
                    "Something went wrong on our side. Please try again in a moment. "
                    "If the problem continues, contact your school administrator."
                ),
                "details": [],
                "path": str(request.url.path),
            }
        },
    )
