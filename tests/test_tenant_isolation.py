import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core.database import (
    async_session_factory,
    reset_tenant_scope,
    set_tenant_scope,
)
from app.modules.identity.models import School
from app.modules.students.models import Student


def _student(school_id, admission_number: str, first_name: str) -> Student:
    return Student(
        school_id=school_id,
        admission_number=admission_number,
        first_name=first_name,
        last_name="Isolation",
        date_of_birth=date(2010, 1, 1),
        gender="M",
        guardian_name="Guardian",
        guardian_email=f"{first_name.lower()}@example.com",
        guardian_phone="+2348000000000",
        enrollment_date=date(2024, 9, 1),
        status="ACTIVE",
    )


@pytest.mark.asyncio
async def test_students_are_isolated_by_row_level_security():
    unique = uuid.uuid4().hex[:8]

    seed_tokens = set_tenant_scope(None, is_super_admin=True)
    try:
        async with async_session_factory() as session:
            school_a = School(
                slug=f"rls-a-{unique}",
                name="RLS School A",
                official_code=f"RLSA-{unique}".upper(),
                state="Oyo",
                city="Oyo",
                address="A Address",
            )
            school_b = School(
                slug=f"rls-b-{unique}",
                name="RLS School B",
                official_code=f"RLSB-{unique}".upper(),
                state="Oyo",
                city="Oyo",
                address="B Address",
            )
            session.add_all([school_a, school_b])
            await session.flush()
            school_a_id, school_b_id = school_a.id, school_b.id

            session.add_all(
                [
                    _student(school_a_id, f"A-{unique}", "Alpha"),
                    _student(school_b_id, f"B-{unique}", "Beta"),
                ]
            )
            await session.commit()
    finally:
        reset_tenant_scope(seed_tokens)

    tenant_a_tokens = set_tenant_scope(school_a_id)
    try:
        async with async_session_factory() as session:
            visible = (await session.execute(select(Student))).scalars().all()
            assert visible, "Tenant A should see its own students"
            assert {row.school_id for row in visible} == {school_a_id}
    finally:
        reset_tenant_scope(tenant_a_tokens)

    async with async_session_factory() as session:
        visible = (await session.execute(select(Student))).scalars().all()
        assert visible == [], "No tenant context must not expose any students"
