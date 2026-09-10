import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base

if TYPE_CHECKING:
    from app.models.code_change import CodeChange


class PullRequest(Base):
    """The result of pushing one CodeChange's diff to a real GitHub branch
    and opening a pull request for it -- the human-approval gate's actual
    output. 1:1 with CodeChange, mirroring TestRun/Review, but unlike
    them, "created" is a genuinely terminal state, not a replaceable one:
    a real PR now exists on GitHub, and there's no sensible meaning to
    "recreate" it the way regenerating a diff or re-running tests replaces
    purely internal data (see app/api/issues.py's create_pull_request
    endpoint). Only "failed" (the attempt didn't actually produce a PR) is
    retried in place.

    status is "queued" | "creating" | "created" | "failed": "failed" means
    the attempt itself broke (a git push or GitHub API error) -- same
    failed/error-style distinction TestRun and Review already make for
    their own status, just with a single terminal failure state here
    since there's no equivalent of "ran but the verdict was negative" for
    opening a PR the way there is for tests failing or changes being
    requested.
    """

    __tablename__ = "pull_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code_change_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("code_changes.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(default="queued")
    error: Mapped[str | None]
    branch_name: Mapped[str | None]
    pr_number: Mapped[int | None]
    pr_url: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    code_change: Mapped["CodeChange"] = relationship(back_populates="pull_request")
