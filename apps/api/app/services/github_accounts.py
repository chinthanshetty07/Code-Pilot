import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_token
from app.models.github_account import GitHubAccount


async def get_access_token(db: AsyncSession, owner_id: uuid.UUID) -> str:
    """The decrypted GitHub access token for a repository's owner. Shared by
    indexing (downloads a tarball) and the Coder agent's workspace (also
    downloads a tarball, to build a local checkout) -- anything else that
    needs to call the GitHub API on a user's behalf belongs here too."""
    result = await db.execute(select(GitHubAccount).where(GitHubAccount.user_id == owner_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("No GitHub account linked for this repository's owner")
    return decrypt_token(account.access_token_encrypted)
