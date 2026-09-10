import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base

if TYPE_CHECKING:
    from app.models.issue import Issue
    from app.models.test_run import TestRun


class CodeChange(Base):
    """The Coder agent's output for one Issue: a unified diff against the
    repository's current default branch, plus a plain-language summary.
    1:1 with Issue, mirroring Plan -- regenerating replaces this row rather
    than accumulating a history of attempts, matching this milestone's
    scope (no versioning/retry-history UI yet)."""

    __tablename__ = "code_changes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), unique=True, index=True
    )
    generation_status: Mapped[str] = mapped_column(default="queued")
    generation_error: Mapped[str | None]
    summary: Mapped[str | None]
    diff: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    issue: Mapped["Issue"] = relationship(back_populates="code_change")
    test_run: Mapped["TestRun | None"] = relationship(
        back_populates="code_change", uselist=False, cascade="all, delete-orphan"
    )
