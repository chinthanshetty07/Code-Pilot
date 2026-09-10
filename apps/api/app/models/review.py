import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base

if TYPE_CHECKING:
    from app.models.code_change import CodeChange


class Review(Base):
    """The Reviewer agent's verdict on one CodeChange, requested once its
    tests have passed. 1:1, mirroring TestRun -- re-requesting replaces
    this row rather than versioning it, same scope reasoning as
    CodeChange/TestRun (see those models).

    status is "queued" | "reviewing" | "approved" | "changes_requested" |
    "failed": approved/changes_requested are the two real verdicts (the
    review actually completed) -- failed means the review *process* itself
    broke (an LLM/provider error, failing to converge), not a judgment
    about the code, the same failed/error-style distinction TestRun makes
    for its own status. error holds the process failure message in that
    case; summary and comments are the LLM's own output, populated only
    once a verdict is actually reached. comments is nullable (not just an
    empty list) so "no review has run yet" stays distinguishable from "it
    ran and found nothing to flag".
    """

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code_change_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("code_changes.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(default="queued")
    error: Mapped[str | None]
    summary: Mapped[str | None]
    comments: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    code_change: Mapped["CodeChange"] = relationship(back_populates="review")
