import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents.coder import create_code_change
from app.agents.planner import create_plan
from app.core.db import async_session_factory
from app.models.code_change import CodeChange
from app.models.issue import Issue
from app.models.repository import Repository
from app.services.indexing import index_repository


async def index_repository_task(ctx: dict, repository_id: str) -> dict:
    async with async_session_factory() as db:
        repository = await db.get(Repository, uuid.UUID(repository_id))
        if repository is None:
            return {"status": "error", "message": "repository not found"}

        await index_repository(db, repository)
        return {
            "status": repository.indexing_status,
            "file_count": repository.file_count,
            "chunk_count": repository.chunk_count,
        }


async def create_plan_task(ctx: dict, issue_id: str) -> dict:
    async with async_session_factory() as db:
        issue = await db.get(Issue, uuid.UUID(issue_id))
        if issue is None:
            return {"status": "error", "message": "issue not found"}

        await create_plan(db, issue)
        return {"status": issue.planning_status}


async def create_code_change_task(ctx: dict, code_change_id: str) -> dict:
    async with async_session_factory() as db:
        result = await db.execute(
            select(CodeChange)
            .options(
                selectinload(CodeChange.issue).selectinload(Issue.plan),
                selectinload(CodeChange.issue).selectinload(Issue.repository),
            )
            .where(CodeChange.id == uuid.UUID(code_change_id))
        )
        code_change = result.scalar_one_or_none()
        if code_change is None:
            return {"status": "error", "message": "code_change not found"}

        await create_code_change(db, code_change)
        return {"status": code_change.generation_status}
