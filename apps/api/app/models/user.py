import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base

if TYPE_CHECKING:
    from app.models.github_account import GitHubAccount
    from app.models.repository import Repository


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(unique=True, index=True)
    email: Mapped[str | None] = mapped_column(unique=True)
    name: Mapped[str | None]
    avatar_url: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    github_account: Mapped["GitHubAccount | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    repositories: Mapped[list["Repository"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
