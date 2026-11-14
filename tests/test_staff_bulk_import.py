import asyncio
import io
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import select

from app.core.database import async_session_factory
from app.main import app
from app.modules.identity import router as identity_router
from app.modules.identity import tasks as identity_tasks
from app.modules.identity.bulk_import import parse_staff_import_file
from app.modules.identity.models import StaffProfile, User
from app.modules.identity.seed import seed_system_permissions


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


async def _create_school_and_admin(client: AsyncClient, suffix: str) -> tuple[str, str]:
    payload = {
        "name": f"Olivet Baptist High School {suffix}",
        "slug": f"olivet-{suffix}",
        "official_code": f"OBHS-{suffix.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": "Olivet Heights, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": f"principal.{suffix}@olivet-test.edu.ng",
        "admin_first_name": "Adeyemi",
        "admin_last_name": "Olufemi",
        "admin_password": "SuperAdminSecurePassword123!",
    }
    response = await client.post("/api/v1/identity/schools", json=payload)
    assert response.status_code == 201, response.text
    school_id = response.json()["id"]

    login = await client.post(
        "/api/v1/identity/login",
        json={
            "email": payload["admin_email"],
            "password": payload["admin_password"],
        },
    )
    assert login.status_code == 200, login.text
    return school_id, login.json()["access_token"]


def test_parse_staff_import_file_supports_csv_and_xlsx():
    headers = [
        "email",
        "first_name",
        "last_name",
        "staff_id_number",
        "role",
        "phone_number",
        "specialization",
    ]
    csv_data = (
        ",".join(headers)
        + "\n"
        + "teacher.csv@olivet-test.edu.ng,Funke,Adebayo,STF-001,TEACHER,08031234567,Chemistry\n"
    )
    parsed_csv = parse_staff_import_file("staff.csv", csv_data.encode())
    assert parsed_csv.is_valid
    assert len(parsed_csv.rows) == 1
    assert parsed_csv.rows[0].data.email == "teacher.csv@olivet-test.edu.ng"

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(headers)
    worksheet.append(
        [
            "teacher.xlsx@olivet-test.edu.ng",
            "Tunde",
            "Bello",
            "STF-002",
            "TEACHER",
            "08039876543",
            "Physics",
        ]
    )
    buffer = io.BytesIO()
    workbook.save(buffer)

    parsed_xlsx = parse_staff_import_file("staff.xlsx", buffer.getvalue())
    assert parsed_xlsx.is_valid
    assert len(parsed_xlsx.rows) == 1
    assert parsed_xlsx.rows[0].data.first_name == "Tunde"


@pytest.mark.asyncio
async def test_staff_bulk_import_queues_and_provisions(monkeypatch):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        _, admin_token = await _create_school_and_admin(client, suffix)
        headers = {"Authorization": f"Bearer {admin_token}"}

        fake_redis = FakeRedis()
        monkeypatch.setattr(identity_router, "get_redis", lambda: fake_redis)
        monkeypatch.setattr(identity_tasks, "get_redis", lambda: fake_redis)

        dispatched: dict = {}

        class FakeTask:
            def apply_async(self, args=None, task_id=None, queue=None):
                dispatched["args"] = args
                dispatched["task_id"] = task_id
                dispatched["queue"] = queue

        monkeypatch.setattr(identity_router, "process_staff_bulk_import", FakeTask())

        queued_email_args: list = []

        class FakeEmailTask:
            def delay(self, *args, **kwargs):
                queued_email_args.append(args)

        monkeypatch.setattr(identity_tasks, "send_staff_invitation_email", FakeEmailTask())

        emails = [
            f"teacher.one.{suffix}@olivet-test.edu.ng",
            f"teacher.two.{suffix}@olivet-test.edu.ng",
        ]
        csv_data = (
            "email,first_name,last_name,staff_id_number,role\n"
            f"{emails[0]},Funke,Adebayo,STF-{suffix}-1,TEACHER\n"
            f"{emails[1]},Tunde,Bello,STF-{suffix}-2,ACADEMIC_OFFICER\n"
        )

        response = await client.post(
            "/api/v1/identity/staff/bulk-import",
            files={"file": ("staff.csv", csv_data.encode(), "text/csv")},
            headers=headers,
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        assert dispatched["task_id"] == task_id
        assert dispatched["queue"] == "bulk_imports"

        result = await asyncio.to_thread(
            identity_tasks.process_staff_bulk_import.run,
            *dispatched["args"],
        )
        assert result["status"] == "completed", result
        assert result["created"] == 2
        assert result["invitations_queued"] == 2
        assert len(queued_email_args) == 2
        assert not any(key.startswith("bulk_import:staff:") for key in fake_redis.values)

        async with async_session_factory() as session:
            users = (
                (await session.execute(select(User).where(User.email.in_(emails)))).scalars().all()
            )
            assert len(users) == 2
            assert all(user.is_active is False for user in users)

            profiles = (
                (
                    await session.execute(
                        select(StaffProfile).where(
                            StaffProfile.user_id.in_([user.id for user in users])
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(profiles) == 2
            assert {profile.employment_status for profile in profiles} == {"INVITED"}

        invitation_token = queued_email_args[0][3]
        verify = await client.get(f"/api/v1/identity/staff/verify-invite?token={invitation_token}")
        assert verify.status_code == 200, verify.text
        assert verify.json()["email"] == emails[0]


@pytest.mark.asyncio
async def test_staff_bulk_import_rejects_invalid_rows_atomically(monkeypatch):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:8]
        _, admin_token = await _create_school_and_admin(client, suffix)
        headers = {"Authorization": f"Bearer {admin_token}"}

        fake_redis = FakeRedis()
        monkeypatch.setattr(identity_router, "get_redis", lambda: fake_redis)
        monkeypatch.setattr(identity_tasks, "get_redis", lambda: fake_redis)

        dispatched: dict = {}

        class FakeTask:
            def apply_async(self, args=None, task_id=None, queue=None):
                dispatched["args"] = args
                dispatched["task_id"] = task_id
                dispatched["queue"] = queue

        monkeypatch.setattr(identity_router, "process_staff_bulk_import", FakeTask())

        valid_email = f"teacher.valid.{suffix}@olivet-test.edu.ng"
        csv_data = (
            "email,first_name,last_name,staff_id_number\n"
            f"{valid_email},Funke,Adebayo,STF-{suffix}-1\n"
            f"not-an-email,Tunde,Bello,STF-{suffix}-2\n"
        )

        response = await client.post(
            "/api/v1/identity/staff/bulk-import",
            files={"file": ("staff.csv", csv_data.encode(), "text/csv")},
            headers=headers,
        )
        assert response.status_code == 202, response.text

        result = await asyncio.to_thread(
            identity_tasks.process_staff_bulk_import.run,
            *dispatched["args"],
        )
        assert result["status"] == "failed_validation", result
        assert result["created"] == 0
        assert result["errors"]

        async with async_session_factory() as session:
            existing = (
                await session.execute(select(User).where(User.email == valid_email))
            ).scalar_one_or_none()
            assert existing is None
