import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.core.db import async_session_factory
from app.models.code_change import CodeChange
from app.models.issue import Issue
from app.models.plan import Plan
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import Review
from app.models.test_run import TestRun
from app.models.user import User
from app.services import pull_requests as pull_requests_module
from app.services.pull_requests import create_pull_request
from app.services.workspace import Workspace

_PULL_REQUEST_LOAD_OPTIONS = (
    selectinload(PullRequest.code_change).selectinload(CodeChange.issue).selectinload(Issue.repository),
    selectinload(PullRequest.code_change).selectinload(CodeChange.issue).selectinload(Issue.plan),
    selectinload(PullRequest.code_change).selectinload(CodeChange.test_run),
    selectinload(PullRequest.code_change).selectinload(CodeChange.review),
)


async def _make_local_workspace() -> Workspace:
    root = Path(tempfile.mkdtemp(prefix="pr-test-workspace-"))
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def greet(name):\n    return f'hello {name}'\n")
    ws = Workspace(root)
    await ws._run_git("init", "-q")
    await ws._run_git("config", "user.email", "test@localhost")
    await ws._run_git("config", "user.name", "Test")
    await ws._run_git("add", "-A")
    await ws._run_git("commit", "-q", "-m", "Initial state")
    return ws


async def _fake_create_workspace(*_args: object, **_kwargs: object) -> Workspace:
    return await _make_local_workspace()


async def _make_bare_remote() -> Path:
    root = Path(tempfile.mkdtemp(prefix="pr-test-bare-remote-"))
    remote = Workspace(root)
    await remote._run_git("init", "-q", "--bare")
    return root


REAL_DIFF = (
    "diff --git a/src/app.py b/src/app.py\n"
    "index 0000000..1111111 100644\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def greet(name):\n"
    "-    return f'hello {name}'\n"
    "+    return f'hi {name}'\n"
)


@pytest.fixture
async def pull_request() -> AsyncIterator[PullRequest]:
    async with async_session_factory() as db:
        user = User(username=f"pr-test-{uuid.uuid4().hex[:8]}")
        db.add(user)
        await db.flush()

        repo = Repository(
            owner_id=user.id, github_id=-1, full_name="pr-test/repo", default_branch="main"
        )
        db.add(repo)
        await db.flush()

        issue = Issue(
            repository_id=repo.id,
            created_by_id=user.id,
            description="The greet function should say hi instead of hello.",
            planning_status="planned",
        )
        db.add(issue)
        await db.flush()

        plan = Plan(
            issue_id=issue.id,
            summary="Say hi instead of hello.",
            relevant_files=[{"file_path": "src/app.py", "reason": "contains greet()"}],
            implementation_steps=["Change the greeting word"],
            tests_to_add_or_change=[],
            risks=[],
        )
        db.add(plan)
        await db.flush()

        code_change = CodeChange(
            issue_id=issue.id,
            generation_status="generated",
            summary="Changed the greeting from hello to hi.",
            diff=REAL_DIFF,
        )
        db.add(code_change)
        await db.flush()

        test_run = TestRun(
            code_change_id=code_change.id,
            status="passed",
            command="pytest",
            output="1 passed",
            exit_code=0,
        )
        db.add(test_run)
        await db.flush()

        created = PullRequest(code_change_id=code_change.id, status="queued")
        db.add(created)
        await db.commit()

        try:
            async with async_session_factory() as fresh_db:
                result = await fresh_db.execute(
                    select(PullRequest)
                    .options(*_PULL_REQUEST_LOAD_OPTIONS)
                    .where(PullRequest.id == created.id)
                )
                yield result.scalar_one()
        finally:
            async with async_session_factory() as cleanup_db:
                await cleanup_db.execute(
                    delete(PullRequest).where(PullRequest.code_change_id == code_change.id)
                )
                await cleanup_db.execute(
                    delete(TestRun).where(TestRun.code_change_id == code_change.id)
                )
                await cleanup_db.execute(
                    delete(Review).where(Review.code_change_id == code_change.id)
                )
                await cleanup_db.execute(delete(CodeChange).where(CodeChange.issue_id == issue.id))
                await cleanup_db.execute(delete(Plan).where(Plan.issue_id == issue.id))
                await cleanup_db.execute(delete(Issue).where(Issue.id == issue.id))
                await cleanup_db.execute(delete(Repository).where(Repository.id == repo.id))
                await cleanup_db.execute(delete(User).where(User.id == user.id))
                await cleanup_db.commit()


async def _reload(pull_request: PullRequest) -> PullRequest:
    async with async_session_factory() as db:
        result = await db.execute(select(PullRequest).where(PullRequest.id == pull_request.id))
        return result.scalar_one()


class _FakeGitHubClient:
    """Stands in for the real GitHub REST call -- there's no way to
    exercise a real `POST /repos/.../pulls` without real GitHub
    credentials (the same standing gap every GitHub-dependent milestone
    has had). The git push side of this flow is still exercised for
    real, against a local bare repo (see _make_bare_remote) -- only the
    one call that can't be, is mocked."""

    def __init__(self, _access_token: str) -> None:
        pass

    async def create_pull_request(self, _full_name: str, **_kwargs: object) -> dict:
        return {"number": 42, "html_url": "https://github.com/pr-test/repo/pull/42"}

    async def aclose(self) -> None:
        pass


def _patch_pull_requests(
    monkeypatch: pytest.MonkeyPatch, remote_root: Path, github_client: type = _FakeGitHubClient
) -> None:
    monkeypatch.setattr(
        pull_requests_module, "get_access_token", AsyncMock(return_value="dummy-token")
    )
    monkeypatch.setattr(pull_requests_module, "create_workspace", _fake_create_workspace)
    monkeypatch.setattr(
        pull_requests_module, "build_authenticated_remote_url", lambda *_a: str(remote_root)
    )
    monkeypatch.setattr(pull_requests_module, "GitHubClient", github_client)


async def test_create_pull_request_pushes_a_real_branch_and_opens_a_pr(
    monkeypatch: pytest.MonkeyPatch, pull_request: PullRequest
) -> None:
    remote_root = await _make_bare_remote()
    try:
        _patch_pull_requests(monkeypatch, remote_root)

        async with async_session_factory() as db:
            db_pr = await db.get(PullRequest, pull_request.id, options=_PULL_REQUEST_LOAD_OPTIONS)
            await create_pull_request(db, db_pr)

        reloaded = await _reload(pull_request)
        assert reloaded.status == "created"
        assert reloaded.error is None
        assert reloaded.pr_number == 42
        assert reloaded.pr_url == "https://github.com/pr-test/repo/pull/42"
        assert reloaded.branch_name is not None
        assert reloaded.branch_name.startswith("codepilot/")

        # The branch, and the actual diffed content, really landed on the
        # "remote" -- not just a mocked return value.
        remote_ws = Workspace(remote_root)
        show = await remote_ws._run_git(
            "show", f"refs/heads/{reloaded.branch_name}:src/app.py"
        )
        assert "hi {name}" in show
    finally:
        shutil.rmtree(remote_root, ignore_errors=True)


async def test_create_pull_request_fails_when_diff_is_empty(
    monkeypatch: pytest.MonkeyPatch, pull_request: PullRequest
) -> None:
    remote_root = await _make_bare_remote()
    try:
        _patch_pull_requests(monkeypatch, remote_root)

        async with async_session_factory() as db:
            db_pr = await db.get(PullRequest, pull_request.id, options=_PULL_REQUEST_LOAD_OPTIONS)
            db_pr.code_change.diff = ""
            await create_pull_request(db, db_pr)

        reloaded = await _reload(pull_request)
        assert reloaded.status == "failed"
        assert "no diff" in (reloaded.error or "").lower()
    finally:
        shutil.rmtree(remote_root, ignore_errors=True)


async def test_create_pull_request_fails_cleanly_when_the_push_fails(
    monkeypatch: pytest.MonkeyPatch, pull_request: PullRequest
) -> None:
    """A remote that doesn't exist (rather than a real, working bare repo)
    -- proves a push failure is caught and recorded like everything else,
    and (via push_branch's own redaction, already covered directly in
    test_workspace.py) never leaks the embedded token into the stored
    error even though this path constructs a real token-bearing URL."""
    monkeypatch.setattr(
        pull_requests_module, "get_access_token", AsyncMock(return_value="a-real-looking-token")
    )
    monkeypatch.setattr(pull_requests_module, "create_workspace", _fake_create_workspace)
    monkeypatch.setattr(pull_requests_module, "GitHubClient", _FakeGitHubClient)
    # Deliberately NOT patching build_authenticated_remote_url -- this
    # exercises the real https://x-access-token:<token>@github.com/...
    # URL construction, pointed at a repo that doesn't exist, so the push
    # fails against a URL that genuinely contains the token.

    async with async_session_factory() as db:
        db_pr = await db.get(PullRequest, pull_request.id, options=_PULL_REQUEST_LOAD_OPTIONS)
        await create_pull_request(db, db_pr)

    reloaded = await _reload(pull_request)
    assert reloaded.status == "failed"
    assert "a-real-looking-token" not in (reloaded.error or "")


async def test_create_pull_request_fails_when_the_github_api_call_fails(
    monkeypatch: pytest.MonkeyPatch, pull_request: PullRequest
) -> None:
    class _FailingGitHubClient(_FakeGitHubClient):
        async def create_pull_request(self, _full_name: str, **_kwargs: object) -> dict:
            raise ValueError("422 Unprocessable Entity: A pull request already exists")

    remote_root = await _make_bare_remote()
    try:
        _patch_pull_requests(monkeypatch, remote_root, github_client=_FailingGitHubClient)

        async with async_session_factory() as db:
            db_pr = await db.get(PullRequest, pull_request.id, options=_PULL_REQUEST_LOAD_OPTIONS)
            await create_pull_request(db, db_pr)

        reloaded = await _reload(pull_request)
        assert reloaded.status == "failed"
        assert "already exists" in (reloaded.error or "")
        # The branch was still pushed for real before the API call failed
        # -- a real, useful side effect isn't undone just because the
        # follow-up PR-open call failed.
        remote_ws = Workspace(remote_root)
        refs = await remote_ws._run_git("branch", "--list", reloaded.branch_name or "")
        assert reloaded.branch_name in refs
    finally:
        shutil.rmtree(remote_root, ignore_errors=True)
