import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class IssueCreateIn(BaseModel):
    description: str = Field(min_length=1, max_length=5000)


class PlanOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    summary: str
    relevant_files: list
    implementation_steps: list
    tests_to_add_or_change: list
    risks: list
    created_at: datetime


class IssueOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    repository_id: uuid.UUID
    description: str
    planning_status: str
    planning_error: str | None
    created_at: datetime
    plan: PlanOut | None
