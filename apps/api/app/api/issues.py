from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.jobs import get_arq_pool
from app.core.rate_limit import rate_limit
from app.core.sessions import get_current_user
from app.models.code_change import CodeChange
from app.models.issue import Issue
from app.models.repository import Repository
from app.models.user import User
from app.schemas.issue import IssueCreateIn, IssueOut

router = APIRouter(tags=["issues"])


async def _get_owned_repository(db: AsyncSession, repository_id: str, user: User) -> Repository:
    repository = await db.get(Repository, repository_id)
    if repository is None or repository.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


async def _get_owned_issue(
    db: AsyncSession, repository_id: str, issue_id: str, user: User
) -> Issue:
    result = await db.execute(
        select(Issue)
        .options(selectinload(Issue.plan), selectinload(Issue.code_change))
        .join(Repository, Issue.repository_id == Repository.id)
        .where(
            Issue.id == issue_id,
            Issue.repository_id == repository_id,
            Repository.owner_id == user.id,
        )
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    return issue


@router.post(
    "/api/repositories/{repository_id}/issues",
    response_model=IssueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_issue(
    repository_id: str,
    payload: IssueCreateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Issue:
    await rate_limit(request, key="create_issue", limit=10, window_seconds=60)

    repository = await _get_owned_repository(db, repository_id, user)

    issue = Issue(
        repository_id=repository.id,
        created_by_id=user.id,
        description=payload.description,
        planning_status="queued",
    )
    db.add(issue)
    await db.commit()
    # Explicitly loads the (currently nonexistent) `plan`/`code_change`
    # relationships so response serialization can read them without an
    # async lazy load, which isn't valid outside an awaited context.
    await db.refresh(issue, attribute_names=["plan", "code_change"])

    pool = await get_arq_pool()
    await pool.enqueue_job("create_plan_task", str(issue.id))

    return issue


@router.get("/api/repositories/{repository_id}/issues", response_model=list[IssueOut])
async def list_issues(
    repository_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Issue]:
    await _get_owned_repository(db, repository_id, user)

    result = await db.execute(
        select(Issue)
        .options(selectinload(Issue.plan), selectinload(Issue.code_change))
        .where(Issue.repository_id == repository_id)
        .order_by(Issue.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/api/repositories/{repository_id}/issues/{issue_id}", response_model=IssueOut)
async def get_issue(
    repository_id: str,
    issue_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Issue:
    return await _get_owned_issue(db, repository_id, issue_id, user)


@router.post(
    "/api/repositories/{repository_id}/issues/{issue_id}/code-changes",
    response_model=IssueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_code_change(
    repository_id: str,
    issue_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Issue:
    await rate_limit(request, key="create_code_change", limit=10, window_seconds=60)

    issue = await _get_owned_issue(db, repository_id, issue_id, user)

    if issue.planning_status != "planned":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This issue doesn't have a completed plan yet",
        )
    if issue.code_change is not None and issue.code_change.generation_status in (
        "queued",
        "generating",
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Code generation is already in progress",
        )

    if issue.code_change is None:
        code_change = CodeChange(issue_id=issue.id, generation_status="queued")
        db.add(code_change)
    else:
        # Regenerating replaces the previous attempt rather than keeping a
        # history of them -- matches this milestone's scope (see CodeChange).
        code_change = issue.code_change
        code_change.generation_status = "queued"
        code_change.generation_error = None
        code_change.summary = None
        code_change.diff = None
    await db.commit()
    await db.refresh(issue, attribute_names=["plan", "code_change"])

    pool = await get_arq_pool()
    await pool.enqueue_job("create_code_change_task", str(code_change.id))

    return issue
