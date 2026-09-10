import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base

if TYPE_CHECKING:
    from app.models.issue import Issue


class Plan(Base):
    """The Planner agent's structured output for one Issue. Kept as a
    handful of JSONB columns rather than normalized tables (a `files`
    table, a `steps` table, ...) -- this is always read and replaced as a
    single unit, never queried by individual sub-fields, so normalizing it
    would just be extra joins for no benefit."""

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), unique=True, index=True
    )
    summary: Mapped[str]
    relevant_files: Mapped[list] = mapped_column(JSONB)
    implementation_steps: Mapped[list] = mapped_column(JSONB)
    tests_to_add_or_change: Mapped[list] = mapped_column(JSONB)
    risks: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    issue: Mapped["Issue"] = relationship(back_populates="plan")
