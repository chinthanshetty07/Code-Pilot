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

    status is "queued" | "running" | "passed" | "failed" | "error":
    passed/failed mean the tests actually ran (exit code 0 or not) --
    error means no verdict could be reached at all (no test command
    detected, a sandbox infrastructure failure, or a timeout). Milestone 8's
    fix loop cares about that distinction: "failed" is something an LLM
    can act on, "error" usually isn't.
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    code_change: Mapped["CodeChange"] = relationship(back_populates="test_run")
