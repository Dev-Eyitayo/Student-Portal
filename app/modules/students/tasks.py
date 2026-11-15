import asyncio
import base64
import logging
from typing import Any
from uuid import UUID

from app.core.celery_app import celery_app
from app.core.database import (
    async_engine,
    async_session_factory,
    reset_tenant_scope,
    set_tenant_scope,
)
from app.core.redis import close_redis, get_redis
from app.modules.audit.services import AuditContext
from app.modules.students import services
from app.modules.students.bulk_import import parse_student_import_file

logger = logging.getLogger(__name__)

STAGING_KEY = "bulk_import:students:{school_id}:{job_id}"
STAGING_TTL_SECONDS = 60 * 60


def _result(school_id: str, status: str, **payload: Any) -> dict[str, Any]:
    return {"status": status, "school_id": school_id, **payload}


@celery_app.task(
    bind=True,
    name="app.modules.students.tasks.process_student_bulk_import",
)
def process_student_bulk_import(
    self,
    job_id: str,
    school_id: str,
    actor_id: str,
    actor_ip: str,
    filename: str,
) -> dict[str, Any]:
    return asyncio.run(
        _process_student_bulk_import(job_id, school_id, actor_id, actor_ip, filename)
    )


async def _process_student_bulk_import(
    job_id: str,
    school_id: str,
    actor_id: str,
    actor_ip: str,
    filename: str,
) -> dict[str, Any]:
    try:
        return await _process_student_bulk_import_inner(
            job_id,
            school_id,
            actor_id,
            actor_ip,
            filename,
        )
    finally:
        await close_redis()
        await async_engine.dispose()


async def _process_student_bulk_import_inner(
    job_id: str,
    school_id: str,
    actor_id: str,
    actor_ip: str,
    filename: str,
) -> dict[str, Any]:
    key = STAGING_KEY.format(school_id=school_id, job_id=job_id)

    try:
        client = get_redis()
        encoded_file = await client.get(key)
        await client.delete(key)
    except Exception:
        logger.exception("Could not read a staged student import from Redis")
        return _result(
            school_id,
            "failed",
            created=0,
            errors=[
                {
                    "row": 0,
                    "message": (
                        "We couldn't read the uploaded student file from temporary storage. "
                        "Please upload it again."
                    ),
                }
            ],
        )

    if not encoded_file:
        return _result(
            school_id,
            "failed",
            created=0,
            errors=[
                {
                    "row": 0,
                    "message": (
                        "The uploaded student file has expired before it could be processed. "
                        "Please upload it again."
                    ),
                }
            ],
        )

    try:
        file_bytes = base64.b64decode(encoded_file)
    except Exception:
        return _result(
            school_id,
            "failed",
            created=0,
            errors=[
                {
                    "row": 0,
                    "message": (
                        "The uploaded student file could not be decoded. Please upload it again."
                    ),
                }
            ],
        )

    parsed = parse_student_import_file(filename, file_bytes)
    if not parsed.is_valid:
        return _result(
            school_id,
            "failed_validation",
            created=0,
            errors=parsed.errors,
        )

    school_uuid = UUID(school_id)
    tokens = set_tenant_scope(school_uuid, False)
    try:
        async with async_session_factory() as session:
            provisioned = await services.provision_student_batch(
                session,
                school_uuid,
                parsed.rows,
                AuditContext(
                    school_id=school_uuid,
                    actor_id=UUID(actor_id),
                    actor_ip=actor_ip,
                ),
            )
    finally:
        reset_tenant_scope(tokens)

    if provisioned.errors:
        return _result(
            school_id,
            "failed_validation",
            created=0,
            errors=provisioned.errors,
        )

    return _result(
        school_id,
        "completed",
        created=provisioned.created,
        errors=[],
    )
