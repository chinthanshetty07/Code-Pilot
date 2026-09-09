from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.code_chunk import CodeChunk  # noqa: E402
from app.models.github_account import GitHubAccount  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402

__all__ = ["Base", "User", "GitHubAccount", "Repository", "CodeChunk"]
