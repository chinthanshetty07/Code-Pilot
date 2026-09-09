import secrets
import uuid

from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.redis import redis_client
from app.models.user import User

settings = get_settings()

SESSION_COOKIE_NAME = "session_id"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7


async def create_session(user_id: uuid.UUID, response: Response) -> None:
    session_id = secrets.token_urlsafe(32)
    await redis_client.setex(f"session:{session_id}", SESSION_TTL_SECONDS, str(user_id))
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        path="/",
    )


async def destroy_session(session_id: str | None, response: Response) -> None:
    if session_id:
        await redis_client.delete(f"session:{session_id}")
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


async def get_current_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> User:
    if session_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_id = await redis_client.get(f"session:{session_id}")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    user = await db.get(User, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user
