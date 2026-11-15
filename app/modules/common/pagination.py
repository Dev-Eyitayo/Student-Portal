from math import ceil
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.common.schemas import PageMeta


async def paginate(
    db: AsyncSession,
    stmt: Select[Any],
    *,
    page: int,
    page_size: int,
) -> tuple[list[Any], PageMeta]:
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = (await db.execute(count_stmt)).scalar_one()
    result = await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    items = list(result.scalars().unique().all())
    total_pages = ceil(total / page_size) if total else 0
    return items, PageMeta(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )
