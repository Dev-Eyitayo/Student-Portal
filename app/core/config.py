from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_asyncpg_url(url: str) -> str:
    """Translate libpq-style parameters into asyncpg-compatible ones.

    Neon connection strings use ``sslmode`` and ``channel_binding``. asyncpg
    expects ``ssl`` and treats unknown query parameters as PostgreSQL server
    settings, so ``channel_binding`` must be removed before connecting.
    """
    if not url.startswith("postgresql+asyncpg://"):
        return url

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.pop("channel_binding", None)
    if "sslmode" in query:
        sslmode = query.pop("sslmode")
        query.setdefault("ssl", sslmode)

    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    SQL_ECHO: bool = False
    APP_NAME: str = "Olivet Academic Records Platform"
    APP_URL: str = "http://localhost:8000"

    # Security
    SECRET_KEY: str = "default-insecure-dev-secret-key-change-in-production-1234567890"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database Configuration - loaded strictly from environment or .env
    POSTGRES_USER: str = "olivet_app"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "olivet_records"

    # Non-superuser role used by the running application so PostgreSQL RLS is
    # actually enforced. Falls back to POSTGRES_USER when not configured.
    APP_POSTGRES_USER: str | None = None
    APP_POSTGRES_PASSWORD: str | None = None

    DATABASE_URL: str | None = None
    MIGRATION_DATABASE_URL: str | None = None
    DATABASE_SYNC_URL: str | None = None

    # Connection pool. Keep these small when connecting through a managed
    # transaction pooler such as Neon's PgBouncer endpoint.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_RECYCLE_SECONDS: int = 300
    DB_SSL_MODE: str | None = None
    # None = auto-detect (0 when the URL contains '-pooler', otherwise 100).
    DB_PREPARED_STATEMENT_CACHE_SIZE: int | None = None

    # Redis & Broker
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_URL: str | None = None
    REDIS_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None
    CELERY_TASK_ALWAYS_EAGER: bool = False
    REDIS_SSL_CERT_REQS: str = "required"
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 5.0
    REDIS_CONNECT_TIMEOUT_SECONDS: float = 5.0
    REDIS_HEALTH_CHECK_INTERVAL_SECONDS: int = 30
    REDIS_MAX_CONNECTIONS: int = 20

    # CORS
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
    ]

    # Asset storage
    STORAGE_PROVIDER: str = "local"
    STORAGE_LOCAL_DIR: str = "var/storage"
    STORAGE_PUBLIC_BASE_URL: str | None = None
    BRANDING_UPLOAD_MAX_BYTES: int = 2 * 1024 * 1024
    CLOUDINARY_CLOUD_NAME: str | None = None
    CLOUDINARY_API_KEY: str | None = None
    CLOUDINARY_API_SECRET: str | None = None
    S3_BUCKET: str | None = None
    S3_REGION: str = "us-east-1"
    S3_ENDPOINT_URL: str | None = None
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None

    # Email Delivery (Provider-Agnostic)
    MAIL_PROVIDER: str = "console"
    MAIL_BASE_URL: str = "https://api.senviok.live"
    MAIL_FROM_NAME: str = "Olivet Academic Records Platform"
    DEFAULT_FROM_EMAIL: str = "records@olivet.edu.ng"
    MAIL_API_KEY: str | None = None
    MAIL_DOMAIN: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def assemble_urls_and_validate_secrets(self) -> "Settings":
        # Build DATABASE_URL dynamically if not directly provided. The runtime
        # URL uses the non-superuser role so Row-Level Security applies.
        if not self.DATABASE_URL:
            runtime_user = self.APP_POSTGRES_USER or self.POSTGRES_USER
            runtime_password = (
                self.APP_POSTGRES_PASSWORD if self.APP_POSTGRES_USER else self.POSTGRES_PASSWORD
            )
            auth = f"{runtime_user}:{runtime_password}@" if runtime_password else f"{runtime_user}@"
            self.DATABASE_URL = f"postgresql+asyncpg://{auth}{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

        # Migrations run as the database owner using the admin credentials.
        if not self.MIGRATION_DATABASE_URL:
            auth = (
                f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
                if self.POSTGRES_PASSWORD
                else f"{self.POSTGRES_USER}@"
            )
            self.MIGRATION_DATABASE_URL = f"postgresql+asyncpg://{auth}{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

        if not self.DATABASE_SYNC_URL:
            auth = (
                f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
                if self.POSTGRES_PASSWORD
                else f"{self.POSTGRES_USER}@"
            )
            self.DATABASE_SYNC_URL = (
                f"postgresql://{auth}{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )

        # Normalize Neon/libpq-style query parameters for asyncpg.
        self.DATABASE_URL = normalize_asyncpg_url(self.DATABASE_URL)
        self.MIGRATION_DATABASE_URL = normalize_asyncpg_url(self.MIGRATION_DATABASE_URL)

        # Build REDIS_URL dynamically if not directly provided
        if not self.REDIS_URL:
            self.REDIS_URL = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

        if not self.REDIS_BROKER_URL:
            self.REDIS_BROKER_URL = self.REDIS_URL
        if not self.CELERY_RESULT_BACKEND:
            self.CELERY_RESULT_BACKEND = self.REDIS_BROKER_URL

        # Enforce non-default secret key outside development and testing
        if self.ENVIRONMENT not in ("development", "testing", "test"):
            if "default-insecure" in self.SECRET_KEY:
                raise ValueError(
                    "SECRET_KEY must be configured with a secure high-entropy key in production."
                )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
