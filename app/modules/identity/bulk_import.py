from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator

MAX_STAFF_IMPORT_BYTES = 5 * 1024 * 1024
MAX_STAFF_IMPORT_ROWS = 2000
ALLOWED_STAFF_ROLES = {"TEACHER", "ACADEMIC_OFFICER", "SCHOOL_ADMIN"}
REQUIRED_COLUMNS = ("email", "first_name", "last_name", "staff_id_number")

HEADER_ALIASES = {
    "staff_id": "staff_id_number",
    "staffid": "staff_id_number",
    "staffidnumber": "staff_id_number",
    "staff_number": "staff_id_number",
    "firstname": "first_name",
    "lastname": "last_name",
    "phone": "phone_number",
    "phonenumber": "phone_number",
    "mobile": "phone_number",
    "mobile_number": "phone_number",
}


class StaffBulkImportRow(BaseModel):
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=64)
    last_name: str = Field(..., min_length=1, max_length=64)
    phone_number: str | None = Field(None, max_length=32)
    role: str = "TEACHER"
    staff_id_number: str = Field(..., min_length=1, max_length=64)
    specialization: str | None = Field(None, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip().lower()

    @field_validator("first_name", "last_name", "staff_id_number", mode="before")
    @classmethod
    def strip_required_text(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()

    @field_validator("phone_number", "specialization", mode="before")
    @classmethod
    def blank_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: Any) -> str:
        role = str(value or "TEACHER").strip().upper()
        if role not in ALLOWED_STAFF_ROLES:
            raise ValueError("Role must be one of TEACHER, ACADEMIC_OFFICER, or SCHOOL_ADMIN.")
        return role


@dataclass
class ParsedStaffRow:
    row_number: int
    data: StaffBulkImportRow


@dataclass
class ParsedStaffImport:
    rows: list[ParsedStaffRow] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _canonicalize_header(value: Any) -> str:
    if value is None:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return HEADER_ALIASES.get(normalized, normalized)


def _row_mapping(headers: list[str], values: tuple[Any, ...]) -> dict[str, Any]:
    return {
        headers[index]: values[index]
        for index in range(min(len(headers), len(values)))
        if headers[index]
    }


def _is_blank_row(values: tuple[Any, ...]) -> bool:
    return not values or all(value is None or str(value).strip() == "" for value in values)


def _read_csv_table(data: bytes) -> tuple[list[str], list[tuple[int, dict[str, Any]]]]:
    text = data.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    try:
        raw_headers = next(reader)
    except StopIteration as exc:
        raise ValueError("empty") from exc

    headers = [_canonicalize_header(header) for header in raw_headers]
    _reject_duplicate_headers(headers)

    rows: list[tuple[int, dict[str, Any]]] = []
    for row_number, values in enumerate(reader, start=2):
        if _is_blank_row(tuple(values)):
            continue
        rows.append((row_number, _row_mapping(headers, tuple(values))))
    return headers, rows


def _read_xlsx_table(data: bytes) -> tuple[list[str], list[tuple[int, dict[str, Any]]]]:
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        iterator = worksheet.iter_rows(values_only=True)
        raw_headers = next(iterator, None)
        if raw_headers is None:
            raise ValueError("empty")

        headers = [_canonicalize_header(header) for header in raw_headers]
        _reject_duplicate_headers(headers)

        rows: list[tuple[int, dict[str, Any]]] = []
        for row_number, values in enumerate(iterator, start=2):
            if _is_blank_row(values):
                continue
            rows.append((row_number, _row_mapping(headers, values)))
        return headers, rows
    finally:
        workbook.close()


def _reject_duplicate_headers(headers: list[str]) -> None:
    populated = [header for header in headers if header]
    if len(populated) != len(set(populated)):
        raise ValueError("duplicate_headers")


def _friendly_validation_message(exc: ValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "__root__")
        if location == "email":
            messages.append("Please enter a valid email address.")
        elif location in ("first_name", "last_name"):
            messages.append(f"Please enter a valid {location.replace('_', ' ')}.")
        elif location == "role":
            messages.append("Role must be one of TEACHER, ACADEMIC_OFFICER, or SCHOOL_ADMIN.")
        elif location:
            messages.append(
                f"{location.replace('_', ' ').capitalize()}: {error.get('msg', 'invalid value')}."
            )
        else:
            messages.append(str(error.get("msg", "Invalid value.")))
    return " ".join(dict.fromkeys(messages))


def parse_staff_import_file(filename: str, data: bytes) -> ParsedStaffImport:
    result = ParsedStaffImport()

    if not data:
        result.errors.append(
            {"row": 1, "message": "This file is empty. Please add staff rows and upload again."}
        )
        return result

    if len(data) > MAX_STAFF_IMPORT_BYTES:
        result.errors.append(
            {
                "row": 1,
                "message": "This file is too large. Please upload a file smaller than 5 MB.",
            }
        )
        return result

    suffix = Path(filename or "").suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        result.errors.append(
            {
                "row": 1,
                "message": "Please upload an Excel (.xlsx) or CSV (.csv) file.",
            }
        )
        return result

    try:
        headers, records = _read_csv_table(data) if suffix == ".csv" else _read_xlsx_table(data)
    except ValueError as exc:
        reason = str(exc)
        if reason == "empty":
            message = "This file is empty. Please add staff rows and upload again."
        elif reason == "duplicate_headers":
            message = (
                "This file has duplicate column names. Please make each column "
                "name unique and upload again."
            )
        else:
            message = (
                "We couldn't read this file. Please make sure it is a valid Excel "
                "(.xlsx) or CSV (.csv) file."
            )
        result.errors.append({"row": 1, "message": message})
        return result

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing_columns:
        result.errors.append(
            {
                "row": 1,
                "message": (
                    "The file is missing these required columns: "
                    f"{', '.join(missing_columns)}. Please add them and upload again."
                ),
            }
        )
        return result

    if len(records) > MAX_STAFF_IMPORT_ROWS:
        result.errors.append(
            {
                "row": 1,
                "message": (
                    f"This file has more than {MAX_STAFF_IMPORT_ROWS:,} staff rows. "
                    "Please split it into smaller files."
                ),
            }
        )
        return result

    if not records:
        result.errors.append({"row": 1, "message": "The file does not contain any staff rows."})
        return result

    for row_number, record in records:
        try:
            validated = StaffBulkImportRow(**record)
        except ValidationError as exc:
            result.errors.append(
                {
                    "row": row_number,
                    "message": _friendly_validation_message(exc),
                }
            )
            continue
        result.rows.append(ParsedStaffRow(row_number=row_number, data=validated))

    return result
