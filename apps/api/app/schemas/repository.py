import uuid
from datetime import datetime

from pydantic import BaseModel


class GithubRepoSummaryOut(BaseModel):
    github_id: int
    full_name: str
    description: str | None
    language: str | None
    private: bool
    default_branch: str


class RepositoryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    github_id: int
    full_name: str
    description: str | None
    language: str | None
    default_branch: str
    private: bool
    indexing_status: str
    indexing_error: str | None
    file_count: int
    chunk_count: int
    indexed_at: datetime | None
    connected_at: datetime


class RepositoryCreateIn(BaseModel):
    github_id: int
