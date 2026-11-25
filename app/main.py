import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

import app.models  # noqa: F401  # register every SQLAlchemy model/mapper at startup
from app.core.config import get_settings
from app.core.database import async_session_factory
from app.core.exceptions import (
    DomainException,
    domain_exception_handler,
    http_exception_handler,
    request_validation_exception_handler,
    unhandled_exception_handler,
)
from app.core.redis import close_redis, init_redis
from app.core.rls import verify_rls_enforcement
from app.modules.academic_structure.router import class_groups_router, dashboard_router
from app.modules.academic_structure.router import router as structure_router
from app.modules.assessments.router import router as assessments_router
from app.modules.audit.router import router as audit_router
from app.modules.computation.router import router as computation_router
from app.modules.distribution.router import (
    publication_distribution_router,
    results_router,
)
from app.modules.identity.router import router as identity_router
from app.modules.identity.seed import seed_system_permissions
from app.modules.publication.router import router as publication_router
from app.modules.students.router import router as students_router
from app.modules.subjects.router import (
    class_subjects_router,
    enrollment_registrations_router,
)
from app.modules.subjects.router import router as subjects_router
from app.modules.timetable.router import router as timetable_router

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Production must never run with a role that bypasses Row-Level Security.
    if settings.ENVIRONMENT not in ("development", "testing", "test"):
        async with async_session_factory() as session:
            await verify_rls_enforcement(session)

    await init_redis()

    # Startup: Seed system permissions if database is reachable
    try:
        async with async_session_factory() as session:
            await seed_system_permissions(session)
    except Exception:
        if settings.ENVIRONMENT not in ("testing", "test"):
            logger.warning("System permission seeding skipped", exc_info=True)

    try:
        yield
    finally:
        await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception Handlers
    app.add_exception_handler(DomainException, domain_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # Mount API Routers
    app.include_router(identity_router, prefix="/api/v1")
    app.include_router(structure_router, prefix="/api/v1")
    app.include_router(class_groups_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")
    app.include_router(students_router, prefix="/api/v1")
    app.include_router(subjects_router, prefix="/api/v1")
    app.include_router(class_subjects_router, prefix="/api/v1")
    app.include_router(enrollment_registrations_router, prefix="/api/v1")
    app.include_router(assessments_router, prefix="/api/v1")
    app.include_router(computation_router, prefix="/api/v1")
    app.include_router(publication_router, prefix="/api/v1")
    app.include_router(publication_distribution_router, prefix="/api/v1")
    app.include_router(results_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(timetable_router, prefix="/api/v1")

    # Healthcheck
    @app.get("/api/v1/health", tags=["System"])
    async def health_check():
        return {
            "success": True,
            "data": {
                "status": "healthy",
                "app": settings.APP_NAME,
                "environment": settings.ENVIRONMENT,
            },
        }

    @app.get("/", tags=["System"])
    async def root():
        return {
            "success": True,
            "data": {"message": f"Welcome to {settings.APP_NAME} API", "docs": "/docs"},
        }

    return app


app = create_app()
