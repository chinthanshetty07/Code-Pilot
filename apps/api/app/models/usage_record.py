import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class UsageRecord(Base):
    """One real LLM call's token usage (Milestone 11) -- written by
    app/services/usage.py's record_llm_usage(), called from every agent
    loop (Planner/Coder/Reviewer) right after each provider.complete().
    Stores raw token counts, not a pre-computed dollar cost: pricing is a
    lookup applied at read time (see usage.py's estimate_cost_usd), so a
    pricing correction or a new model never needs a backfill migration --
    the stored facts stay valid, only the derived number changes.

    Deliberately NOT 1:1 with anything (no unique constraint) -- a single
    agent run makes several LLM calls (one per turn), and this is meant to
    capture each one, not just a final total. repository_id is set
    whenever known (every row currently has one, since only agent calls
    are tracked -- see this milestone's own scope note: embeddings/
    indexing usage is deliberately out of scope, both providers' embedding
    responses don't expose comparable per-call token counts). issue_id is
    additionally set for the three agent roles (planner/coder/reviewer),
    which are always issue-scoped.
    """

    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    issue_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), index=True
    )
    # "planner" | "coder" | "reviewer" -- left as a free string (not a DB
    # enum) for the same reason every other status-ish field in this
    # project is, e.g. CodeChange.generation_status.
    agent: Mapped[str]
    provider: Mapped[str]  # "groq" | "gemini"
    model: Mapped[str]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
