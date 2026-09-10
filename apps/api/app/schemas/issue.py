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


class TestRunOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    status: str
    command: str | None
    output: str | None
    exit_code: int | None
    fix_attempts: int
    created_at: datetime


class ReviewCommentOut(BaseModel):
    model_config = {"from_attributes": True}

    file_path: str
    severity: str
    comment: str


class ReviewOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    status: str
    error: str | None
    summary: str | None
    comments: list[ReviewCommentOut] | None
    created_at: datetime


class CodeChangeOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    generation_status: str
    generation_error: str | None
    summary: str | None
    diff: str | None
    created_at: datetime
    test_run: TestRunOut | None
    review: ReviewOut | None


class IssueOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    repository_id: uuid.UUID
    description: str
    planning_status: str
    planning_error: str | None
    created_at: datetime
    plan: PlanOut | None
    code_change: CodeChangeOut | None
