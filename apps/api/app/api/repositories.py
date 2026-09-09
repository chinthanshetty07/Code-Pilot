import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_token
from app.core.db import get_db
from app.core.sessions import get_current_user
from app.github.client import GitHubClient
from app.models.github_account import GitHubAccount
from app.models.repository import Repository
from app.models.user import User
from app.schemas.repository import GithubRepoSummaryOut, RepositoryCreateIn, RepositoryOut

router = APIRouter(tags=["repositories"])


async def _github_client_for(user: User, db: AsyncSession) -> GitHubClient:
    result = await db.execute(select(GitHubAccount).where(GitHubAccount.user_id == user.id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No GitHub account linked"
        )
    return GitHubClient(decrypt_token(account.access_token_encrypted))


@router.get("/api/github/repos", response_model=list[GithubRepoSummaryOut])
async def list_github_repos(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    client = await _github_client_for(user, db)
    try:
        repos = await client.list_repositories()
    finally:
        await client.aclose()

    return [
        {
            "github_id": r["id"],
            "full_name": r["full_name"],
            "description": r.get("description"),
            "language": r.get("language"),
            "private": r["private"],
            "default_branch": r["default_branch"],
        }
        for r in repos
    ]


@router.get("/api/repositories", response_model=list[RepositoryOut])
async def list_connected_repositories(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Repository]:
    result = await db.execute(select(Repository).where(Repository.owner_id == user.id))
    return list(result.scalars().all())


@router.post("/api/repositories", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
async def connect_repository(
    payload: RepositoryCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Repository:
    existing = await db.execute(
        select(Repository).where(
            Repository.owner_id == user.id, Repository.github_id == payload.github_id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Repository already connected"
        )

    client = await _github_client_for(user, db)
    try:
        match = await client.get_repository_by_id(payload.github_id)
    except httpx.HTTPStatusError as exc:
        await client.aclose()
        if exc.response.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found on GitHub"
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Repository not accessible with your GitHub permissions",
        ) from exc
    else:
        await client.aclose()

    repository = Repository(
        owner_id=user.id,
        github_id=match["id"],
        full_name=match["full_name"],
        description=match.get("description"),
        language=match.get("language"),
        default_branch=match["default_branch"],
        private=match["private"],
    )
    db.add(repository)
    await db.commit()
    await db.refresh(repository)
    return repository


@router.get("/api/repositories/{repository_id}", response_model=RepositoryOut)
async def get_repository(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Repository:
    repository = await db.get(Repository, repository_id)
    if repository is None or repository.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository
