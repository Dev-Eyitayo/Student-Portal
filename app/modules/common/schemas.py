from math import ceil
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


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def build(cls, *, page: int, page_size: int, total: int) -> "PageMeta":
        return cls(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=ceil(total / page_size) if total else 0,
        )


class Page[T](BaseModel):
    items: list[T]
    meta: PageMeta
