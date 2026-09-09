from fastapi import HTTPException, Request, status

from app.core.redis import redis_client


async def rate_limit(request: Request, key: str, limit: int, window_seconds: int) -> None:
    client_ip = request.client.host if request.client else "unknown"
    redis_key = f"ratelimit:{key}:{client_ip}"

    count = await redis_client.incr(redis_key)
    if count == 1:
        await redis_client.expire(redis_key, window_seconds)

    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later",
        )
