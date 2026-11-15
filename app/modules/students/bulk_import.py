from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator, model_validator

MAX_STUDENT_IMPORT_BYTES = 5 * 1024 * 1024
MAX_STUDENT_IMPORT_ROWS = 5000
REQUIRED_COLUMNS = (
    "admission_number",
    "first_name",
    "last_name",
    "date_of_birth",
    "gender",
    "guardian_name",
    "guardian_email",
    "guardian_phone",
    "enrollment_date",
)

HEADER_ALIASES = {
    "admission_no": "admission_number",
    "admissionnumber": "admission_number",
    "admission": "admission_number",
    "firstname": "first_name",
    "lastname": "last_name",
    "middlename": "middle_name",
    "dateofbirth": "date_of_birth",
    "dob": "date_of_birth",
    "birth_date": "date_of_birth",
    "guardian": "guardian_name",
    "parent_name": "guardian_name",
    "guardian_email_address": "guardian_email",
    "parent_email": "guardian_email",
    "guardian_phone_number": "guardian_phone",
    "parent_phone": "guardian_phone",
    "admission_date": "enrollment_date",
    "enrollmentdate": "enrollment_date",
}

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")


class StudentBulkImportRow(BaseModel):
    admission_number: str = Field(..., min_length=1, max_length=64)
    first_name: str = Field(..., min_length=1, max_length=64)
    middle_name: str | None = Field(None, max_length=64)
    last_name: str = Field(..., min_length=1, max_length=64)
    date_of_birth: date
    gender: str = Field(..., min_length=1, max_length=10)
    guardian_name: str = Field(..., min_length=2, max_length=128)
    guardian_email: EmailStr
    guardian_phone: str = Field(..., min_length=5, max_length=32)
    enrollment_date: date

    @field_validator("admission_number", mode="before")
    @classmethod
    def normalize_admission_number(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip().upper()

    @field_validator("first_name", "last_name", "guardian_name", "guardian_phone", mode="before")
    @classmethod
    def strip_required_text(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()

    @field_validator("middle_name", mode="before")
    @classmethod
    def blank_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("guardian_email", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip().lower()

    @field_validator("date_of_birth", "enrollment_date", mode="before")
    @classmethod
    def parse_date(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return value
        for date_format in DATE_FORMATS:
            try:
                return datetime.strptime(text, date_format).date()
            except ValueError:
                continue
        return value

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, value: Any) -> Any:
        if value is None:
            return value
        normalized = str(value).strip().upper()
        if normalized in {"M", "MALE"}:
            return "M"
        if normalized in {"F", "FEMALE"}:
            return "F"
        raise ValueError("Please enter M for male or F for female.")

    @model_validator(mode="after")
    def validate_dates(self) -> StudentBulkImportRow:
        if self.date_of_birth >= self.enrollment_date:
            raise ValueError("The date of birth must be before the enrollment date.")
        return self


@dataclass
class ParsedStudentRow:
    row_number: int
    data: StudentBulkImportRow


@dataclass
class ParsedStudentImport:
    rows: list[ParsedStudentRow] = field(default_factory=list)
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


def _reject_duplicate_headers(headers: list[str]) -> None:
    populated = [header for header in headers if header]
    if len(populated) != len(set(populated)):
        raise ValueError("duplicate_headers")


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


def _friendly_validation_message(exc: ValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "__root__")
        if location == "guardian_email":
            messages.append("Please enter a valid guardian email address.")
        elif location in ("date_of_birth", "enrollment_date"):
            messages.append(
                f"Please enter a valid {location.replace('_', ' ')} (for example, 2024-09-09)."
            )
        elif location == "gender":
            messages.append("Please enter M for male or F for female.")
        elif location == "admission_number":
            messages.append("Please enter a valid admission number.")
        elif location:
            messages.append(
                f"{location.replace('_', ' ').capitalize()}: {error.get('msg', 'invalid value')}."
            )
        else:
            messages.append(str(error.get("msg", "Invalid value.")))
    return " ".join(dict.fromkeys(messages))


def parse_student_import_file(filename: str, data: bytes) -> ParsedStudentImport:
    result = ParsedStudentImport()

    if not data:
        result.errors.append(
            {"row": 1, "message": "This file is empty. Please add student rows and upload again."}
        )
        return result

    if len(data) > MAX_STUDENT_IMPORT_BYTES:
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
            message = "This file is empty. Please add student rows and upload again."
        elif reason == "duplicate_headers":
            message = (
                "This file has duplicate column names. Please make each column name unique "
                "and upload again."
            )
        else:
            message = (
                "We couldn't read this file. Please make sure it is a valid Excel (.xlsx) or "
                "CSV (.csv) file."
            )
        result.errors.append({"row": 1, "message": message})
        return result
    except Exception:
        result.errors.append(
            {
                "row": 1,
                "message": (
                    "We couldn't read this file. Please make sure it is a valid Excel (.xlsx) or "
                    "CSV (.csv) file."
                ),
            }
        )
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

    if len(records) > MAX_STUDENT_IMPORT_ROWS:
        result.errors.append(
            {
                "row": 1,
                "message": (
                    f"This file has more than {MAX_STUDENT_IMPORT_ROWS:,} student rows. "
                    "Please split it into smaller files."
                ),
            }
        )
        return result

    if not records:
        result.errors.append({"row": 1, "message": "The file does not contain any student rows."})
        return result

    for row_number, record in records:
        try:
            validated = StudentBulkImportRow(**record)
        except ValidationError as exc:
            result.errors.append(
                {
                    "row": row_number,
                    "message": _friendly_validation_message(exc),
                }
            )
            continue
        result.rows.append(ParsedStudentRow(row_number=row_number, data=validated))

    return result
