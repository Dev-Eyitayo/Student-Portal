from typing import Any

from pydantic import BaseModel, Field


class SuccessResponse[T](BaseModel):
    """Standard success envelope returned by every API endpoint."""

    success: bool = True
    data: T


class ErrorResponse(BaseModel):
    """Standard error envelope returned by every API endpoint."""

    class ErrorBody(BaseModel):
        code: str
        message: str
        details: list[dict[str, Any]] = Field(default_factory=list)
        path: str

    error: ErrorBody


class MessageResponse(BaseModel):
    message: str
