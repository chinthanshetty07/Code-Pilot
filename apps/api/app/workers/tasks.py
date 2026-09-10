import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents.coder import create_code_change
from app.agents.planner import create_plan
from app.agents.reviewer import create_review
from app.core.db import async_session_factory
from app.models.code_change import CodeChange
from app.models.issue import Issue
from app.models.repository import Repository
from app.models.review import Review
from app.models.test_run import TestRun
from app.services.indexing import index_repository
from app.services.test_runner import run_tests


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


async def create_test_run_task(ctx: dict, test_run_id: str) -> dict:
    async with async_session_factory() as db:
        result = await db.execute(
            select(TestRun)
            .options(
                selectinload(TestRun.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.repository),
                # Milestone 8's fix loop reads issue.plan too (for context
                # in a fix attempt's prompt) -- without this, that access
                # is a lazy load outside an awaited context, which crashes
                # every real test run with a MissingGreenlet error, not
                # just ones that reach the fix loop (run_tests reads it
                # unconditionally, right after loading the issue).
                selectinload(TestRun.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.plan),
            )
            .where(TestRun.id == uuid.UUID(test_run_id))
        )
        test_run = result.scalar_one_or_none()
        if test_run is None:
            return {"status": "error", "message": "test_run not found"}

        await run_tests(db, test_run)
        return {"status": test_run.status}


async def create_review_task(ctx: dict, review_id: str) -> dict:
    async with async_session_factory() as db:
        result = await db.execute(
            select(Review)
            .options(
                # Same shape as create_test_run_task's eager-load chain, and
                # for the same reason (see its comment): every relationship
                # create_review actually reads -- issue.repository,
                # issue.plan, and code_change.test_run -- must be listed
                # here explicitly, or accessing it is a lazy load outside an
                # awaited context and crashes with MissingGreenlet.
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.repository),
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.plan),
                selectinload(Review.code_change).selectinload(CodeChange.test_run),
            )
            .where(Review.id == uuid.UUID(review_id))
        )
        review = result.scalar_one_or_none()
        if review is None:
            return {"status": "error", "message": "review not found"}

        await create_review(db, review)
        return {"status": review.status}
