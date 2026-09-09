from redis import asyncio as redis

from app.core.config import get_settings

settings = get_settings()

redis_client = redis.from_url(settings.redis_url, decode_responses=True)


async def check_redis_connection() -> bool:
    try:
        return bool(await redis_client.ping())
    except Exception:
        return False
