import secrets

from fastapi import APIRouter, Cookie, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from app.core.config import get_settings
from app.core.crypto import encrypt_token
from app.core.db import get_db
from app.core.errors import AppError
from app.core.rate_limit import rate_limit
from app.core.sessions import (
    SESSION_COOKIE_NAME,
    create_session,
    destroy_session,
    get_current_user,
)
from app.github.client import GitHubClient, build_authorize_url, exchange_code_for_token
from app.models.github_account import GitHubAccount
from app.models.user import User
from app.schemas.auth import AuthUserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()

STATE_COOKIE_NAME = "oauth_state"


def _callback_redirect_uri() -> str:
    return f"{settings.api_base_url}/api/auth/github/callback"


@router.get("/github/login")
async def github_login(request: Request) -> RedirectResponse:
    await rate_limit(request, key="auth_login", limit=20, window_seconds=60)

    if not settings.github_client_id or not settings.github_client_secret:
        raise AppError(
            code="GITHUB_OAUTH_NOT_CONFIGURED",
            message="GitHub OAuth is not configured on the server "
            "(set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)",
            status_code=503,
        )

    state = secrets.token_urlsafe(24)
    url = build_authorize_url(state, _callback_redirect_uri())
    response = RedirectResponse(url, status_code=307)
    response.set_cookie(
        STATE_COOKIE_NAME, state, max_age=600, httponly=True, samesite="lax", path="/"
    )
    return response


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
    oauth_state: str | None = Cookie(default=None, alias=STATE_COOKIE_NAME),
) -> RedirectResponse:
    await rate_limit(request, key="auth_callback", limit=20, window_seconds=60)

    if not oauth_state or oauth_state != state:
        return RedirectResponse(
            f"{settings.frontend_url}/?error=oauth_state_mismatch", status_code=307
        )

    try:
        access_token = await exchange_code_for_token(code, _callback_redirect_uri())
        client = GitHubClient(access_token)
        try:
            profile = await client.get_authenticated_user()
            email = profile.get("email") or await client.get_primary_email()
        finally:
            await client.aclose()
    except Exception:
        return RedirectResponse(
            f"{settings.frontend_url}/?error=github_oauth_failed", status_code=307
        )

    github_user_id = profile["id"]
    username = profile["login"]

    result = await db.execute(
        select(GitHubAccount).where(GitHubAccount.github_user_id == github_user_id)
    )
    github_account = result.scalar_one_or_none()

    if github_account is None:
        user = User(
            username=username,
            email=email,
            name=profile.get("name"),
            avatar_url=profile.get("avatar_url"),
        )
        db.add(user)
        await db.flush()
        github_account = GitHubAccount(
            user_id=user.id,
            github_user_id=github_user_id,
            github_username=username,
            access_token_encrypted=encrypt_token(access_token),
            scopes="repo read:user user:email",
        )
        db.add(github_account)
    else:
        user = await db.get(User, github_account.user_id)
        user.username = username
        user.email = email or user.email
        user.name = profile.get("name")
        user.avatar_url = profile.get("avatar_url")
        github_account.access_token_encrypted = encrypt_token(access_token)
        github_account.github_username = username

    await db.commit()

    response = RedirectResponse(f"{settings.frontend_url}/repositories", status_code=307)
    await create_session(user.id, response)
    response.delete_cookie(STATE_COOKIE_NAME, path="/")
    return response


@router.get("/me", response_model=AuthUserOut)
async def read_current_user(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> None:
    await destroy_session(session_id, response)
