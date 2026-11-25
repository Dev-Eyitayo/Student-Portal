from datetime import date

import pytest
from pydantic import ValidationError

from app.modules.identity.bulk_import import StaffBulkImportRow, parse_staff_import_file
from app.modules.identity.schemas import MeUpdateRequest, StaffInviteRequest, StaffUpdateRequest
from app.modules.students.bulk_import import StudentBulkImportRow, parse_student_import_file
from app.modules.students.schemas import Gender, StudentCreateRequest, StudentUpdateRequest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("08031234567", "08031234567"),
        ("08123456789", "08123456789"),
        ("07012345678", "07012345678"),
        ("09012345678", "09012345678"),
        ("09112345678", "09112345678"),
        ("+2348031234567", "+2348031234567"),
        ("2348031234567", "2348031234567"),
        ("+2348000000000", "+2348000000000"),
        (" 0803 123 4567 ", "08031234567"),
        ("+234-803-123-4567", "+2348031234567"),
    ],
)
def test_valid_nigerian_phone_numbers_accepted_across_schemas(raw: str, expected: str) -> None:
    invite = StaffInviteRequest(
        email="teacher@olivet.edu.ng",
        first_name="Funke",
        last_name="Adebayo",
        phone_number=raw,
        staff_id_number="STF-001",
    )
    assert invite.phone_number == expected

    me_update = MeUpdateRequest(phone_number=raw)
    assert me_update.phone_number == expected

    staff_update = StaffUpdateRequest(phone_number=raw)
    assert staff_update.phone_number == expected

    staff_row = StaffBulkImportRow(
        email="teacher@olivet.edu.ng",
        first_name="Funke",
        last_name="Adebayo",
        phone_number=raw,
        staff_id_number="STF-001",
    )
    assert staff_row.phone_number == expected

    student_create = StudentCreateRequest(
        admission_number="OBHS/2025/001",
        first_name="Tunde",
        last_name="Bello",
        date_of_birth=date(2012, 5, 10),
        gender=Gender.M,
        guardian_name="Mr Bello",
        guardian_email="parent@olivet.edu.ng",
        guardian_phone=raw,
        enrollment_date=date(2024, 9, 9),
    )
    assert student_create.guardian_phone == expected

    student_update = StudentUpdateRequest(guardian_phone=raw)
    assert student_update.guardian_phone == expected

    student_row = StudentBulkImportRow(
        admission_number="OBHS/2025/001",
        first_name="Tunde",
        last_name="Bello",
        date_of_birth=date(2012, 5, 10),
        gender="M",
        guardian_name="Mr Bello",
        guardian_email="parent@olivet.edu.ng",
        guardian_phone=raw,
        enrollment_date=date(2024, 9, 9),
    )
    assert student_row.guardian_phone == expected


@pytest.mark.parametrize(
    "invalid",
    [
        "0803123456",  # too short (10 digits)
        "080312345678",  # too long (12 digits)
        "+14155552671",  # US number
        "+447911123456",  # UK number
        "05012345678",  # invalid Nigerian prefix
        "08212345678",  # invalid Nigerian prefix
        "0803abcdefg",  # non-numeric
    ],
)
def test_invalid_phone_numbers_rejected_across_schemas(invalid: str) -> None:
    with pytest.raises(ValidationError, match="Nigerian phone number"):
        StaffInviteRequest(
            email="teacher@olivet.edu.ng",
            first_name="Funke",
            last_name="Adebayo",
            phone_number=invalid,
            staff_id_number="STF-001",
        )

    with pytest.raises(ValidationError, match="Nigerian phone number"):
        MeUpdateRequest(phone_number=invalid)

    with pytest.raises(ValidationError, match="Nigerian phone number"):
        StaffUpdateRequest(phone_number=invalid)

    with pytest.raises(ValidationError, match="Nigerian phone number"):
        StaffBulkImportRow(
            email="teacher@olivet.edu.ng",
            first_name="Funke",
            last_name="Adebayo",
            phone_number=invalid,
            staff_id_number="STF-001",
        )

    with pytest.raises(ValidationError, match="Nigerian phone number"):
        StudentCreateRequest(
            admission_number="OBHS/2025/001",
            first_name="Tunde",
            last_name="Bello",
            date_of_birth=date(2012, 5, 10),
            gender=Gender.M,
            guardian_name="Mr Bello",
            guardian_email="parent@olivet.edu.ng",
            guardian_phone=invalid,
            enrollment_date=date(2024, 9, 9),
        )

    with pytest.raises(ValidationError, match="Nigerian phone number"):
        StudentUpdateRequest(guardian_phone=invalid)

    with pytest.raises(ValidationError, match="Nigerian phone number"):
        StudentBulkImportRow(
            admission_number="OBHS/2025/001",
            first_name="Tunde",
            last_name="Bello",
            date_of_birth=date(2012, 5, 10),
            gender="M",
            guardian_name="Mr Bello",
            guardian_email="parent@olivet.edu.ng",
            guardian_phone=invalid,
            enrollment_date=date(2024, 9, 9),
        )


def test_optional_and_blank_phone_handling() -> None:
    for blank in (None, "", "   "):
        assert (
            StaffInviteRequest(
                email="teacher@olivet.edu.ng",
                first_name="Funke",
                last_name="Adebayo",
                phone_number=blank,
                staff_id_number="STF-001",
            ).phone_number
            is None
        )
        assert MeUpdateRequest(phone_number=blank).phone_number is None
        assert StaffUpdateRequest(phone_number=blank).phone_number is None

    # StudentUpdateRequest allows None (omitted), but rejects blank string
    assert StudentUpdateRequest(guardian_phone=None).guardian_phone is None
    with pytest.raises(ValidationError, match="Nigerian phone number"):
        StudentUpdateRequest(guardian_phone="")


def test_bulk_import_parsers_validate_nigerian_phones() -> None:
    staff_csv = (
        b"email,first_name,last_name,staff_id_number,role,phone_number,specialization\n"
        b"t1@olivet.edu.ng,Funke,Adebayo,STF-001,TEACHER,+14155552671,Chemistry\n"
    )
    parsed_staff = parse_staff_import_file("staff.csv", staff_csv)
    assert not parsed_staff.is_valid
    assert "Nigerian phone number" in parsed_staff.errors[0]["message"]

    student_csv = (
        b"admission_number,first_name,middle_name,last_name,date_of_birth,gender,"
        b"guardian_name,guardian_email,guardian_phone,enrollment_date\n"
        b"OBHS/001,Tunde,,Bello,2012-03-14,M,Mr Bello,parent@test.ng,+447911123456,2024-09-09\n"
    )
    parsed_student = parse_student_import_file("students.csv", student_csv)
    assert not parsed_student.is_valid
    assert "Nigerian phone number" in parsed_student.errors[0]["message"]
