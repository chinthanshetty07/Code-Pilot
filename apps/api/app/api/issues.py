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
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import Review
from app.models.test_run import TestRun
from app.models.user import User
from app.schemas.issue import IssueCreateIn, IssueOut
from app.schemas.usage import UsageSummaryOut
from app.services.usage import UsageSummary, get_issue_usage_summary

router = APIRouter(tags=["issues"])

# Full eager-load chain for an Issue's nested plan/code_change/test_run/
# review/pull_request -- used everywhere an Issue is queried, since
# IssueOut always serializes all five and none of them support an async
# lazy load outside an awaited call.
_ISSUE_LOAD_OPTIONS = (
    selectinload(Issue.plan),
    selectinload(Issue.code_change).selectinload(CodeChange.test_run),
    selectinload(Issue.code_change).selectinload(CodeChange.review),
    selectinload(Issue.code_change).selectinload(CodeChange.pull_request),
)


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
        .options(*_ISSUE_LOAD_OPTIONS)
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
        .options(*_ISSUE_LOAD_OPTIONS)
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
    existing_test_run = issue.code_change.test_run if issue.code_change is not None else None
    if existing_test_run is not None and existing_test_run.status in (
        "queued",
        "running",
        "fixing",
    ):
        # Regenerating discards the code_change's test_run below (cascade),
        # which would otherwise race a worker job that's still actively
        # writing to that same row -- most likely now that Milestone 8's fix
        # loop can keep one test run job alive for several minutes.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A test run is still in progress for the current code -- wait for it to "
            "finish before regenerating",
        )
    existing_review = issue.code_change.review if issue.code_change is not None else None
    if existing_review is not None and existing_review.status in ("queued", "reviewing"):
        # Same race as the test_run check above, now for the Reviewer's job.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A review is still in progress for the current code -- wait for it to "
            "finish before regenerating",
        )
    existing_pr = issue.code_change.pull_request if issue.code_change is not None else None
    if existing_pr is not None and existing_pr.status in ("queued", "creating"):
        # Same race as the checks above, now for the pull request job.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pull request is still being created for the current code -- wait for "
            "it to finish before regenerating",
        )
    if existing_pr is not None and existing_pr.status == "created":
        # Unlike test_run/review, a *created* pull_request can't be safely
        # discarded by regenerating: a real PR now exists on GitHub, and
        # there's no way to "un-open" it from here -- cascading it away
        # would just orphan a real PR with no record of it left in
        # CodePilot. Regeneration is blocked outright rather than allowed
        # to silently detach from it (see PullRequest's own docstring).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A pull request has already been created for this code ({existing_pr.pr_url}) "
            "-- CodePilot won't regenerate code out from under an open PR",
        )

    if issue.code_change is None:
        code_change = CodeChange(issue_id=issue.id, generation_status="queued")
        db.add(code_change)
    else:
        # Regenerating replaces the previous attempt rather than keeping a
        # history of them -- matches this milestone's scope (see CodeChange).
        # Any test_run/review/pull_request for the old diff is discarded too
        # (cascade, via setting the relationship to None) -- a stale result
        # for code that no longer exists would be actively misleading, not
        # just unhelpful. Safe to do unconditionally for pull_request here
        # specifically because the guard above already guarantees it's
        # either None or a "failed" attempt by this point -- nothing real
        # on GitHub to orphan (see PullRequest's own status docstring for
        # why a *created* one gets a hard block instead of reaching here).
        code_change = issue.code_change
        code_change.generation_status = "queued"
        code_change.generation_error = None
        code_change.summary = None
        code_change.diff = None
        code_change.test_run = None
        code_change.review = None
        code_change.pull_request = None
    await db.commit()
    await db.refresh(issue, attribute_names=["plan", "code_change"])

    pool = await get_arq_pool()
    await pool.enqueue_job("create_code_change_task", str(code_change.id))

    return issue


@router.post(
    "/api/repositories/{repository_id}/issues/{issue_id}/test-runs",
    response_model=IssueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_test_run(
    repository_id: str,
    issue_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Issue:
    await rate_limit(request, key="create_test_run", limit=10, window_seconds=60)

    issue = await _get_owned_issue(db, repository_id, issue_id, user)

    if issue.code_change is None or issue.code_change.generation_status != "generated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This issue doesn't have generated code to test yet",
        )
    existing = issue.code_change.test_run
    if existing is not None and existing.status in ("queued", "running", "fixing"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A test run is already in progress"
        )

    if existing is None:
        test_run = TestRun(code_change_id=issue.code_change.id, status="queued")
        db.add(test_run)
    else:
        # Re-running replaces the previous result rather than keeping a
        # history of attempts -- same reasoning as CodeChange regeneration.
        test_run = existing
        test_run.status = "queued"
        test_run.command = None
        test_run.output = None
        test_run.exit_code = None
        test_run.fix_attempts = 0
    await db.commit()
    await db.refresh(issue, attribute_names=["plan", "code_change"])
    await db.refresh(issue.code_change, attribute_names=["test_run"])

    pool = await get_arq_pool()
    await pool.enqueue_job("create_test_run_task", str(test_run.id))

    return issue


@router.post(
    "/api/repositories/{repository_id}/issues/{issue_id}/reviews",
    response_model=IssueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_review(
    repository_id: str,
    issue_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Issue:
    await rate_limit(request, key="create_review", limit=10, window_seconds=60)

    issue = await _get_owned_issue(db, repository_id, issue_id, user)

    code_change = issue.code_change
    test_run = code_change.test_run if code_change is not None else None
    if code_change is None or test_run is None or test_run.status != "passed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This issue doesn't have passing tests to review yet",
        )
    existing = code_change.review
    if existing is not None and existing.status in ("queued", "reviewing"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A review is already in progress"
        )

    if existing is None:
        review = Review(code_change_id=code_change.id, status="queued")
        db.add(review)
    else:
        # Re-requesting replaces the previous verdict rather than keeping a
        # history of attempts -- same reasoning as CodeChange regeneration
        # and TestRun re-running.
        review = existing
        review.status = "queued"
        review.error = None
        review.summary = None
        review.comments = None
    await db.commit()
    await db.refresh(issue, attribute_names=["plan", "code_change"])
    await db.refresh(issue.code_change, attribute_names=["review"])

    pool = await get_arq_pool()
    await pool.enqueue_job("create_review_task", str(review.id))

    return issue


@router.post(
    "/api/repositories/{repository_id}/issues/{issue_id}/pull-requests",
    response_model=IssueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_pull_request(
    repository_id: str,
    issue_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Issue:
    await rate_limit(request, key="create_pull_request", limit=10, window_seconds=60)

    issue = await _get_owned_issue(db, repository_id, issue_id, user)

    code_change = issue.code_change
    test_run = code_change.test_run if code_change is not None else None
    # Gated on tests passing, same as requesting a review -- deliberately
    # NOT gated on review.status == "approved". The AI reviewer is
    # advisory; the human clicking this button is the actual approval
    # the pipeline's "Human Approval" stage refers to, and that authority
    # stays with them even if the AI requested changes (the frontend
    # confirms before proceeding in that case, but doesn't block it).
    if code_change is None or test_run is None or test_run.status != "passed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This issue doesn't have passing tests to open a pull request for yet",
        )
    existing = code_change.pull_request
    if existing is not None and existing.status in ("queued", "creating"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pull request is already being created",
        )
    if existing is not None and existing.status == "created":
        # A real PR already exists -- nothing to redo (see PullRequest's
        # own status docstring for why this is a true terminal state,
        # unlike test_run/review's replaceable terminal states).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A pull request has already been created for this code: {existing.pr_url}",
        )

    if existing is None:
        pull_request = PullRequest(code_change_id=code_change.id, status="queued")
        db.add(pull_request)
    else:
        # Retrying a failed attempt replaces it in place -- same reasoning
        # as CodeChange regeneration/TestRun re-running/Review
        # re-requesting. Only reachable here with status == "failed" (the
        # checks above already ruled out queued/creating/created).
        pull_request = existing
        pull_request.status = "queued"
        pull_request.error = None
        pull_request.branch_name = None
        pull_request.pr_number = None
        pull_request.pr_url = None
    await db.commit()
    await db.refresh(issue, attribute_names=["plan", "code_change"])
    await db.refresh(issue.code_change, attribute_names=["pull_request"])

    pool = await get_arq_pool()
    await pool.enqueue_job("create_pull_request_task", str(pull_request.id))

    return issue


@router.get(
    "/api/repositories/{repository_id}/issues/{issue_id}/usage", response_model=UsageSummaryOut
)
async def get_issue_usage(
    repository_id: str,
    issue_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UsageSummary:
    issue = await _get_owned_issue(db, repository_id, issue_id, user)
    return await get_issue_usage_summary(db, issue.id)
