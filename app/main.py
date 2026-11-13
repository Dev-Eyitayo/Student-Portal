from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import get_settings
from app.core.exceptions import DomainException, domain_exception_handler

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: e.g. verify db connection or redis availability if needed
    yield
    # Shutdown


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url="/api/v1/redoc",
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
                "docs": "/api/v1/docs"
            }
        }

    return app


app = create_app()
