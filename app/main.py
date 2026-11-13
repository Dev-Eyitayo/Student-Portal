import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import get_settings
from app.core.database import async_session_factory
from app.core.exceptions import DomainException, domain_exception_handler
from app.core.redis import close_redis, init_redis
from app.core.rls import verify_rls_enforcement
import app.models  # noqa: F401  # register every SQLAlchemy model/mapper at startup
from app.modules.identity.router import router as identity_router
from app.modules.identity.seed import seed_system_permissions

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

    # Mount API Routers
    app.include_router(identity_router, prefix="/api/v1")

    # Healthcheck
    @app.get("/api/v1/health", tags=["System"])
    async def health_check():
        return {
            "success": True,
            "data": {
                "status": "healthy",
                "app": settings.APP_NAME,
                "environment": settings.ENVIRONMENT,
            }
        }

    @app.get("/", tags=["System"])
    async def root():
        return {
            "success": True,
            "data": {
                "message": f"Welcome to {settings.APP_NAME} API",
                "docs": "/docs"
            }
        }

    return app


app = create_app()
