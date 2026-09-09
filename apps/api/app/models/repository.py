import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base

if TYPE_CHECKING:
    from app.models.user import User


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("owner_id", "github_id", name="uq_repository_owner_github_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    github_id: Mapped[int] = mapped_column(index=True)
    full_name: Mapped[str]
    description: Mapped[str | None]
    language: Mapped[str | None]
    default_branch: Mapped[str]
    private: Mapped[bool] = mapped_column(default=False)
    indexing_status: Mapped[str] = mapped_column(default="not_indexed")
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    owner: Mapped["User"] = relationship(back_populates="repositories")
