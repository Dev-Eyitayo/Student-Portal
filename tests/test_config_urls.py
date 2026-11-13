from app.core.config import normalize_asyncpg_url


def test_normalizes_neon_libpq_parameters_for_asyncpg():
    url = (
        "postgresql+asyncpg://user:pass@ep-xxxx-pooler.region.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )

    assert normalize_asyncpg_url(url) == (
        "postgresql+asyncpg://user:pass@ep-xxxx-pooler.region.aws.neon.tech/neondb"
        "?ssl=require"
    )


def test_keeps_explicit_ssl_parameter():
    url = (
        "postgresql+asyncpg://user:pass@host/db"
        "?ssl=verify-full&sslmode=require&channel_binding=require"
    )

    assert normalize_asyncpg_url(url) == (
        "postgresql+asyncpg://user:pass@host/db?ssl=verify-full"
    )


def test_leaves_sync_urls_unchanged():
    url = "postgresql://user:pass@host/db?sslmode=require&channel_binding=require"

    assert normalize_asyncpg_url(url) == url
