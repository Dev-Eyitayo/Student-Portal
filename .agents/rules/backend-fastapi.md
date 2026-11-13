---
description: Code style and backend development conventions for FastAPI, SQLAlchemy 2.0, and Pydantic v2
always_on: true
---

# Backend FastAPI & SQLAlchemy Conventions

Follow these patterns when implementing features:

1. **Service Layer Separation:**
   - Routes (`router.py`) handle HTTP request validation and response formatting.
   - Domain logic and database writes live in `services.py`.
   - Data models live in `models.py`.
   - Schemas live in `schemas.py`.

2. **SQLAlchemy 2.0 Async Declarative Syntax:**
   - Use `Mapped[...]` and `mapped_column(...)`.
   - Use `select(...)` and `execute(...)` with `AsyncSession`.
   - Always extend `TenantMixin` for school-scoped models and `TimestampMixin` for audit timestamps.

3. **Pydantic v2 Validation:**
   - Use `@model_validator(mode="after")` or `Field(...)` constraints.
   - Enforce Decimal precision with `max_digits=5, decimal_places=2`.
   - Ensure explicit serialization schemas for all API endpoints.

4. **Background Tasks:**
   - Asynchronous tasks (bulk CSV/Excel imports, emails, SMS notifications) must be enqueued as Celery tasks in `tasks.py`.
   - Always pass `school_id` as the first argument in Celery tasks to re-establish the tenant context.

5. **Client-Side Document Assembly:**
   - Report cards are assembled and printed to PDF on the client side from verified backend JSON payloads.
   - Do not add server-side PDF compilers or cloud object storage for rendered reports.
