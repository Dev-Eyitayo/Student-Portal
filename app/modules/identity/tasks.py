import asyncio
import base64
import logging
from html import escape
from typing import Any
from uuid import UUID

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import (
    async_engine,
    async_session_factory,
    reset_tenant_scope,
    set_tenant_scope,
)
from app.core.mail import EmailDeliveryError, mail_client
from app.core.redis import close_redis, get_redis
from app.modules.identity import services
from app.modules.identity.bulk_import import parse_staff_import_file

logger = logging.getLogger(__name__)

STAGING_KEY = "bulk_import:staff:{school_id}:{job_id}"
STAGING_TTL_SECONDS = 60 * 60


def _result(school_id: str, status: str, **payload: Any) -> dict[str, Any]:
    return {"status": status, "school_id": school_id, **payload}


@celery_app.task(
    bind=True,
    name="app.modules.identity.tasks.send_staff_invitation_email",
    autoretry_for=(EmailDeliveryError,),
    retry_backoff=True,
    max_retries=3,
)
def send_staff_invitation_email(
    self,
    email: str,
    first_name: str,
    school_name: str,
    invitation_token: str,
) -> dict[str, Any]:
    app_url = get_settings().APP_URL.rstrip("/")
    invite_url = f"{app_url}/invite?token={invitation_token}"
    safe_name = escape(first_name)
    safe_school = escape(school_name)
    html = (
        f"<p>Hello {safe_name},</p>"
        f"<p>You have been invited to join <strong>{safe_school}</strong> on Olivet.</p>"
        f'<p><a href="{escape(invite_url)}">Accept your invitation</a> (valid for 72 hours).</p>'
    )
    message_id = mail_client.send_sync(
        to_email=email,
        to_name=first_name,
        subject=f"You are invited to {school_name}",
        html=html,
    )
    return {"status": "sent", "email": email, "message_id": message_id}


@celery_app.task(
    bind=True,
    name="app.modules.identity.tasks.process_staff_bulk_import",
)
def process_staff_bulk_import(
    self,
    job_id: str,
    school_id: str,
    granter_id: str,
    filename: str,
) -> dict[str, Any]:
    return asyncio.run(_process_staff_bulk_import(job_id, school_id, granter_id, filename))


async def _process_staff_bulk_import(
    job_id: str,
    school_id: str,
    granter_id: str,
    filename: str,
) -> dict[str, Any]:
    try:
        return await _process_staff_bulk_import_inner(job_id, school_id, granter_id, filename)
    finally:
        await close_redis()
        await async_engine.dispose()


async def _process_staff_bulk_import_inner(
    job_id: str,
    school_id: str,
    granter_id: str,
    filename: str,
) -> dict[str, Any]:
    key = STAGING_KEY.format(school_id=school_id, job_id=job_id)

    try:
        client = get_redis()
        encoded_file = await client.get(key)
        await client.delete(key)
    except Exception:
        logger.exception("Could not read a staged staff import from Redis")
        return _result(
            school_id,
            "failed",
            created=0,
            invitations_queued=0,
            errors=[
                {
                    "row": 0,
                    "message": (
                        "We couldn't read the uploaded staff file from temporary "
                        "storage. Please upload it again."
                    ),
                }
            ],
        )

    if not encoded_file:
        return _result(
            school_id,
            "failed",
            created=0,
            invitations_queued=0,
            errors=[
                {
                    "row": 0,
                    "message": (
                        "The uploaded staff file has expired before it could be "
                        "processed. Please upload it again."
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
            invitations_queued=0,
            errors=[
                {
                    "row": 0,
                    "message": (
                        "The uploaded staff file could not be decoded. Please upload it again."
                    ),
                }
            ],
        )

    parsed = parse_staff_import_file(filename, file_bytes)
    if not parsed.is_valid:
        return _result(
            school_id,
            "failed_validation",
            created=0,
            invitations_queued=0,
            errors=parsed.errors,
        )

    school_uuid = UUID(school_id)
    tokens = set_tenant_scope(school_uuid, False)
    try:
        async with async_session_factory() as session:
            provisioned = await services.provision_staff_batch(
                session, school_uuid, UUID(granter_id), parsed.rows
            )
    finally:
        reset_tenant_scope(tokens)

    if provisioned.errors:
        return _result(
            school_id,
            "failed_validation",
            created=0,
            invitations_queued=0,
            errors=provisioned.errors,
        )

    first_names = {row.data.email.lower(): row.data.first_name for row in parsed.rows}
    invitations_queued = 0
    email_errors = 0
    for invitation in provisioned.invitations:
        try:
            send_staff_invitation_email.delay(
                invitation.email,
                first_names.get(invitation.email.lower(), "there"),
                provisioned.school_name,
                invitation.invitation_token,
            )
            invitations_queued += 1
        except Exception:
            email_errors += 1
            logger.exception("Could not queue a staff invitation email")

    return _result(
        school_id,
        "completed",
        created=provisioned.created,
        invitations_queued=invitations_queued,
        email_errors=email_errors,
        errors=[],
    )
