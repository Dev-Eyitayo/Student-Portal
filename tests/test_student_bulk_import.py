import asyncio
import io
import time
import uuid
from datetime import date
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import func, select, text

from app.core.database import async_session_factory
from app.main import app
from app.modules.identity.seed import seed_system_permissions
from app.modules.students import router as students_router
from app.modules.students import tasks as student_tasks
from app.modules.students.bulk_import import parse_student_import_file
from app.modules.students.models import Student


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.values.pop(key, None) is not None else 0


@pytest.fixture(autouse=True)
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


async def _create_school_and_admin(client: AsyncClient, suffix: str) -> str:
    payload = {
        "name": f"Olivet Bulk Student School {suffix}",
        "slug": f"olivet-bulk-student-{suffix}",
        "official_code": f"OBHS-BS-{suffix.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": "Olivet Heights, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": f"bulk.student.principal.{suffix}@olivet-test.edu.ng",
        "admin_first_name": "Adeyemi",
        "admin_last_name": "Olufemi",
        "admin_password": "SuperAdminSecurePassword123!",
    }
    response = await client.post("/api/v1/identity/schools", json=payload)
    assert response.status_code == 201, response.text

    login = await client.post(
        "/api/v1/identity/login",
        json={
            "email": payload["admin_email"],
            "password": payload["admin_password"],
        },
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


def _csv_row(admission_number: str, index: int, *, gender: str = "M") -> str:
    return (
        f"{admission_number},Tunde,Oluwaseun,Bello,2012-01-15,{gender},Mr Bello,"
        f"guardian{index}@olivet-test.edu.ng,08031234567,2024-09-09\n"
    )


def _student_csv(rows: list[str]) -> bytes:
    header = (
        "admission_number,first_name,middle_name,last_name,date_of_birth,gender,"
        "guardian_name,guardian_email,guardian_phone,enrollment_date\n"
    )
    return (header + "".join(rows)).encode()


def test_parse_student_import_file_supports_csv_and_xlsx():
    csv_data = _student_csv(
        [
            "OBHS/CSV/0001,Funke,,Adebayo,2012-03-14,F,Mrs Adebayo,parent@test.ng,08031234567,2024-09-09\n"
        ]
    )
    parsed_csv = parse_student_import_file("students.csv", csv_data)
    assert parsed_csv.is_valid, parsed_csv.errors
    assert parsed_csv.rows[0].data.admission_number == "OBHS/CSV/0001"
    assert parsed_csv.rows[0].data.gender == "F"

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        [
            "admission_number",
            "first_name",
            "middle_name",
            "last_name",
            "date_of_birth",
            "gender",
            "guardian_name",
            "guardian_email",
            "guardian_phone",
            "enrollment_date",
        ]
    )
    worksheet.append(
        [
            "OBHS/XLSX/0002",
            "Tunde",
            None,
            "Bello",
            date(2011, 5, 20),
            "MALE",
            "Mr Bello",
            "xlsx.parent@test.ng",
            "08039876543",
            date(2024, 9, 9),
        ]
    )
    buffer = io.BytesIO()
    workbook.save(buffer)

    parsed_xlsx = parse_student_import_file("students.xlsx", buffer.getvalue())
    assert parsed_xlsx.is_valid, parsed_xlsx.errors
    assert parsed_xlsx.rows[0].data.date_of_birth == date(2011, 5, 20)
    assert parsed_xlsx.rows[0].data.gender == "M"


def test_parse_student_import_file_rejects_corrupt_xlsx():
    parsed = parse_student_import_file("students.xlsx", b"not-a-valid-workbook")

    assert not parsed.is_valid
    assert parsed.errors == [
        {
            "row": 1,
            "message": (
                "We couldn't read this file. Please make sure it is a valid Excel (.xlsx) or "
                "CSV (.csv) file."
            ),
        }
    ]


@pytest.mark.asyncio
async def test_student_bulk_import_queues_and_provisions(monkeypatch):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        token = await _create_school_and_admin(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}

        fake_redis = FakeRedis()
        monkeypatch.setattr(students_router, "get_redis", lambda: fake_redis)
        monkeypatch.setattr(student_tasks, "get_redis", lambda: fake_redis)

        dispatched: dict = {}

        class FakeTask:
            def apply_async(self, args=None, task_id=None, queue=None):
                dispatched["args"] = args
                dispatched["task_id"] = task_id
                dispatched["queue"] = queue

        monkeypatch.setattr(students_router, "process_student_bulk_import", FakeTask())

        admissions = [f"OBHS/BULK/{suffix.upper()}/0001", f"OBHS/BULK/{suffix.upper()}/0002"]
        csv_data = _student_csv(
            [
                _csv_row(admissions[0], 1),
                _csv_row(admissions[1], 2, gender="FEMALE"),
            ]
        )

        response = await client.post(
            "/api/v1/students/bulk-import",
            files={"file": ("students.csv", csv_data, "text/csv")},
            headers=headers,
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["data"]["task_id"]
        assert dispatched["task_id"] == task_id
        assert dispatched["queue"] == "bulk_imports"

        result = await asyncio.to_thread(
            student_tasks.process_student_bulk_import.run,
            *dispatched["args"],
        )
        assert result["status"] == "completed", result
        assert result["created"] == 2
        assert not any(key.startswith("bulk_import:students:") for key in fake_redis.values)

        bulk_school_id = UUID(str(dispatched["args"][1]))
        async with async_session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.current_school_id', :school_id, true)"),
                {"school_id": str(bulk_school_id)},
            )
            students = (
                (
                    await session.execute(
                        select(Student).where(Student.admission_number.in_(admissions))
                    )
                )
                .scalars()
                .all()
            )
            assert len(students) == 2
            assert {student.status for student in students} == {"ADMITTED"}
            assert {student.gender for student in students} == {"M", "F"}


@pytest.mark.asyncio
async def test_student_bulk_import_rejects_invalid_rows_atomically(monkeypatch):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        token = await _create_school_and_admin(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}

        fake_redis = FakeRedis()
        monkeypatch.setattr(students_router, "get_redis", lambda: fake_redis)
        monkeypatch.setattr(student_tasks, "get_redis", lambda: fake_redis)

        dispatched: dict = {}

        class FakeTask:
            def apply_async(self, args=None, task_id=None, queue=None):
                dispatched["args"] = args
                dispatched["task_id"] = task_id
                dispatched["queue"] = queue

        monkeypatch.setattr(students_router, "process_student_bulk_import", FakeTask())

        valid_admission = f"OBHS/INVALID/{suffix}/0001"
        csv_data = _student_csv(
            [
                _csv_row(valid_admission, 1),
                _csv_row(f"OBHS/INVALID/{suffix}/0002", 2, gender="UNKNOWN"),
            ]
        )

        response = await client.post(
            "/api/v1/students/bulk-import",
            files={"file": ("students.csv", csv_data, "text/csv")},
            headers=headers,
        )
        assert response.status_code == 202, response.text

        result = await asyncio.to_thread(
            student_tasks.process_student_bulk_import.run,
            *dispatched["args"],
        )
        assert result["status"] == "failed_validation", result
        assert result["created"] == 0
        assert result["errors"]

        invalid_school_id = UUID(str(dispatched["args"][1]))
        async with async_session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.current_school_id', :school_id, true)"),
                {"school_id": str(invalid_school_id)},
            )
            existing = (
                await session.execute(
                    select(Student).where(
                        Student.school_id == invalid_school_id,
                        Student.admission_number == valid_admission,
                    )
                )
            ).scalar_one_or_none()
            assert existing is None


@pytest.mark.asyncio
async def test_student_bulk_import_handles_one_thousand_rows(monkeypatch):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        token = await _create_school_and_admin(client, suffix)
        headers = {"Authorization": f"Bearer {token}"}
        fake_redis = FakeRedis()
        monkeypatch.setattr(students_router, "get_redis", lambda: fake_redis)
        monkeypatch.setattr(student_tasks, "get_redis", lambda: fake_redis)

        dispatched: dict = {}

        class FakeTask:
            def apply_async(self, args=None, task_id=None, queue=None):
                dispatched["args"] = args

        monkeypatch.setattr(students_router, "process_student_bulk_import", FakeTask())

        rows = [_csv_row(f"OBHS/PERF/{suffix}/{index:04d}", index) for index in range(1, 1001)]
        response = await client.post(
            "/api/v1/students/bulk-import",
            files={"file": ("students.csv", _student_csv(rows), "text/csv")},
            headers=headers,
        )
        assert response.status_code == 202, response.text

        started = time.perf_counter()
        result = await asyncio.to_thread(
            student_tasks.process_student_bulk_import.run,
            *dispatched["args"],
        )
        elapsed = time.perf_counter() - started

        assert result["status"] == "completed", result
        assert result["created"] == 1000
        assert elapsed < 15

        dispatched_school_id = UUID(dispatched["args"][1])
        async with async_session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.current_school_id', :school_id, true)"),
                {"school_id": str(dispatched_school_id)},
            )
            total = await session.scalar(
                select(func.count()).where(
                    Student.school_id == dispatched_school_id,
                    Student.admission_number.like(f"OBHS/PERF/{suffix.upper()}/%"),
                )
            )
            assert total == 1000
