import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base

if TYPE_CHECKING:
    from app.models.code_change import CodeChange


class TestRun(Base):
    """The Sandbox Test Runner's result for one CodeChange. 1:1, mirroring
    Plan/CodeChange -- re-running replaces this row rather than versioning
    it, same scope reasoning as CodeChange (see that model).

    status is "queued" | "running" | "fixing" | "passed" | "failed" |
    "error": passed/failed mean the tests actually ran (exit code 0 or
    not) -- error means no verdict could be reached at all (no test
    command detected, a sandbox infrastructure failure, or a timeout).
    "fixing" is the Milestone 8 fix loop actually working (the Coder agent
    generating a fix, in between two "running"s) -- it's pending, like
    queued/running, just with its own label so the UI can say what's
    actually happening instead of a generic spinner.

    "failed" is what the fix loop acts on -- "error" usually isn't (see
    app/services/test_runner.py). fix_attempts counts how many fix passes
    this test_run has gone through, capped at
    app.services.test_runner.MAX_FIX_ATTEMPTS; 0 means either no fix was
    needed (passed first try) or none has run yet.
    """

    __tablename__ = "test_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code_change_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("code_changes.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(default="queued")
    command: Mapped[str | None]
    output: Mapped[str | None]
    exit_code: Mapped[int | None]
    fix_attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    code_change: Mapped["CodeChange"] = relationship(back_populates="test_run")
