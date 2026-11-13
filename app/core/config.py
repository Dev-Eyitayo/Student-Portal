from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    APP_NAME: str = "Olivet Academic Records Platform"
    APP_URL: str = "http://localhost:8000"

    # Security
    SECRET_KEY: str = "default-insecure-dev-secret-key-change-in-production-1234567890"
    ALGORITHM: str = "HS256"  # Uses RS256 with key files in prod; HS256 for local dev
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://olivet_app:olivet_secure_pass@localhost:5432/olivet_records"
    DATABASE_SYNC_URL: str = "postgresql://olivet_app:olivet_secure_pass@localhost:5432/olivet_records"

    # Redis & Broker
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
