import logging
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_client: Optional[aioredis.Redis] = None


def get_redis() -> aioredis.Redis:
    """Return the process-wide async Redis client, creating it on first use."""
    global _client
    if _client is not None:
        return _client

    kwargs: dict = {
        "decode_responses": True,
        "socket_connect_timeout": settings.REDIS_CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": settings.REDIS_SOCKET_TIMEOUT_SECONDS,
        "health_check_interval": settings.REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
        "max_connections": settings.REDIS_MAX_CONNECTIONS,
    }
    if settings.REDIS_URL.startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = settings.REDIS_SSL_CERT_REQS

    _client = aioredis.from_url(settings.REDIS_URL, **kwargs)
    return _client


async def init_redis() -> None:
    """Prepare the shared Redis client without blocking startup on a ping."""
    get_redis()


async def close_redis() -> None:
    """Close the shared Redis client during application shutdown."""
    global _client
    if _client is None:
        return
    try:
        await _client.aclose()
    except Exception:
        logger.warning("Failed to close the Redis client cleanly", exc_info=True)
    finally:
        _client = None
