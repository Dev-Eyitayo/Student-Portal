from typing import Any, Dict, List, Optional
from fastapi import Request, status
from fastapi.responses import JSONResponse


class DomainException(Exception):
    """Base domain exception with standardized error structure."""
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
    def __init__(self, message: str = "Access denied: Resource does not belong to your tenant."):
        super().__init__(
            code="TENANT_MISMATCH",
            message=message,
            status_code=status.HTTP_403_FORBIDDEN
        )


class InsufficientPermissionException(DomainException):
    def __init__(self, permission_code: str, scope: Optional[str] = None):
        msg = f"Missing required permission: '{permission_code}'"
        if scope:
            msg += f" for scope '{scope}'"
        super().__init__(
            code="INSUFFICIENT_PERMISSIONS",
            message=msg,
            status_code=status.HTTP_403_FORBIDDEN
        )


class ResourceNotFoundException(DomainException):
    def __init__(self, resource: str, identifier: Any):
        super().__init__(
            code="RESOURCE_NOT_FOUND",
            message=f"{resource} with identifier '{identifier}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND
        )


class ValidationException(DomainException):
    def __init__(self, message: str, details: Optional[List[Dict[str, Any]]] = None):
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details
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
