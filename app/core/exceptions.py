from typing import Any, Dict, List, Optional
from fastapi import Request, status
from fastapi.responses import JSONResponse

# Human-readable translations for technical permission codes
PERMISSION_FRIENDLY_NAMES: Dict[str, str] = {
    "scores:enter": "enter or edit scores",
    "scores:submit": "submit scores for review",
    "scores:approve": "approve scores",
    "scores:unlock": "unlock a locked score sheet",
    "students:enroll": "enroll students into classes",
    "students:create": "register new students",
    "reports:publish": "publish academic term results",
    "classes:enter_remarks": "enter form master comments and conduct remarks",
    "structure:manage": "manage academic sessions, terms, and classes",
}


class DomainException(Exception):
    """Base domain exception with standardized, human-friendly error messages."""
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[List[Dict[str, Any]]] = None
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []
        super().__init__(message)


class TenantMismatchException(DomainException):
    def __init__(
        self,
        message: str = "We couldn't find this record in your school's account. Please check that you're signed in to the right school."
    ):
        super().__init__(
            code="TENANT_MISMATCH",
            message=message,
            status_code=status.HTTP_403_FORBIDDEN
        )


class InsufficientPermissionException(DomainException):
    def __init__(self, permission_code: str, scope: Optional[str] = None):
        action = PERMISSION_FRIENDLY_NAMES.get(permission_code, f"perform this action ({permission_code})")
        if scope:
            msg = f"You don't have permission to {action} for {scope}. If you need this access, please ask your school administrator or academic officer."
        else:
            msg = f"You don't have permission to {action}. If you need this access, please ask your school administrator or academic officer."

        super().__init__(
            code="INSUFFICIENT_PERMISSIONS",
            message=msg,
            status_code=status.HTTP_403_FORBIDDEN,
            details=[{"permission_required": permission_code, "scope": scope}] if scope else [{"permission_required": permission_code}]
        )


class ResourceNotFoundException(DomainException):
    def __init__(self, resource: str, identifier: Any):
        super().__init__(
            code="RESOURCE_NOT_FOUND",
            message=f"We couldn't find the {resource.lower()} you were looking for. Please check the details and try again.",
            status_code=status.HTTP_404_NOT_FOUND,
            details=[{"resource": resource, "identifier": str(identifier)}]
        )


class ValidationException(DomainException):
    def __init__(self, message: str, details: Optional[List[Dict[str, Any]]] = None):
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details
        )


class ScoresIncompleteException(DomainException):
    def __init__(self, subject_name: str, missing_count: int):
        super().__init__(
            code="SCORES_INCOMPLETE",
            message=f"Scores cannot be submitted yet because {missing_count} student(s) in {subject_name} are still missing a score. Please enter their scores, or mark them as absent or exempt before submitting.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=[{"subject": subject_name, "missing_students_count": missing_count}]
        )


async def domain_exception_handler(request: Request, exc: DomainException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "path": str(request.url.path)
            }
        }
    )
