import asyncio
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
from app.modules.timetable import services

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.modules.timetable.tasks.generate_timetable_task",
)
def generate_timetable_task(self, school_id: str, timetable_id: str) -> dict[str, Any]:
    return asyncio.run(_run_generation(UUID(school_id), UUID(timetable_id)))


async def _run_generation(school_id: UUID, timetable_id: UUID) -> dict[str, Any]:
    tokens = set_tenant_scope(school_id=school_id, is_super_admin=False)
    try:
        async with async_session_factory() as db:
            try:
                summary = await services.run_timetable_generation(db, school_id, timetable_id)
                return {"status": "COMPLETED" if summary.get("success") else "FAILED", **summary}
            except Exception as exc:
                logger.exception("Timetable generation failed for %s", timetable_id)
                return {"status": "FAILED", "error": str(exc)}
    finally:
        reset_tenant_scope(tokens)
        await async_engine.dispose()
