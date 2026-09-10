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
from app.models.repository import Repository
from app.models.test_run import TestRun
from app.models.user import User
from app.services import test_runner as test_runner_module
from app.services.test_runner import run_tests
from app.services.workspace import Workspace

# run_tests() itself needs code_change -> issue -> {repository, plan} eager-
# loaded (plan too, since Milestone 8's fix loop reads issue.plan) -- shared
# here rather than repeated per test, mirroring app/api/issues.py's
# _ISSUE_LOAD_OPTIONS and app/workers/tasks.py's matching production chain.
_TEST_RUN_LOAD_OPTIONS = (
    selectinload(TestRun.code_change).selectinload(CodeChange.issue).selectinload(Issue.repository),
    selectinload(TestRun.code_change).selectinload(CodeChange.issue).selectinload(Issue.plan),
)


async def _make_local_workspace() -> Workspace:
    root = Path(tempfile.mkdtemp(prefix="test-runner-workspace-"))
    (root / "test_math.py").write_text("def test_addition():\n    assert 1 + 1 == 2\n")
    ws = Workspace(root)
    await ws._run_git("init", "-q")
    await ws._run_git("config", "user.email", "test@localhost")
    await ws._run_git("config", "user.name", "Test")
    await ws._run_git("add", "-A")
    await ws._run_git("commit", "-q", "-m", "Initial state")
    return ws


async def _fake_create_workspace(*_args: object, **_kwargs: object) -> Workspace:
    return await _make_local_workspace()


def _patch_workspace_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        test_runner_module, "get_access_token", AsyncMock(return_value="dummy-token")
    )
    monkeypatch.setattr(test_runner_module, "create_workspace", _fake_create_workspace)


@pytest.fixture
async def test_run() -> AsyncIterator[TestRun]:
    async with async_session_factory() as db:
        user = User(username=f"test-runner-test-{uuid.uuid4().hex[:8]}")
        db.add(user)
        await db.flush()

        repo = Repository(
            owner_id=user.id, github_id=-1, full_name="test-runner-test/repo", default_branch="main"
        )
        db.add(repo)
        await db.flush()

        issue = Issue(
            repository_id=repo.id,
            created_by_id=user.id,
            description="irrelevant for this test",
            planning_status="planned",
        )
        db.add(issue)
        await db.flush()

        code_change = CodeChange(
            issue_id=issue.id, generation_status="generated", diff="", summary="n/a"
        )
        db.add(code_change)
        await db.flush()

        created = TestRun(code_change_id=code_change.id, status="queued")
        db.add(created)
        await db.commit()

        try:
            async with async_session_factory() as fresh_db:
                result = await fresh_db.execute(
                    select(TestRun)
                    .options(*_TEST_RUN_LOAD_OPTIONS)
                    .where(TestRun.id == created.id)
                )
                yield result.scalar_one()
        finally:
            async with async_session_factory() as cleanup_db:
                await cleanup_db.execute(
                    delete(TestRun).where(TestRun.code_change_id == code_change.id)
                )
                await cleanup_db.execute(delete(CodeChange).where(CodeChange.id == code_change.id))
                await cleanup_db.execute(delete(Issue).where(Issue.id == issue.id))
                await cleanup_db.execute(delete(Repository).where(Repository.id == repo.id))
                await cleanup_db.execute(delete(User).where(User.id == user.id))
                await cleanup_db.commit()


async def _reload(test_run: TestRun) -> TestRun:
    async with async_session_factory() as db:
        result = await db.execute(select(TestRun).where(TestRun.id == test_run.id))
        return result.scalar_one()


async def test_run_tests_passes_a_real_pytest_suite(
    monkeypatch: pytest.MonkeyPatch, test_run: TestRun
) -> None:
    _patch_workspace_creation(monkeypatch)

    async with async_session_factory() as db:
        db_test_run = await db.get(
            TestRun,
            test_run.id,
            options=_TEST_RUN_LOAD_OPTIONS,
        )
        await run_tests(db, db_test_run)

    reloaded = await _reload(test_run)
    assert reloaded.status == "passed"
    assert reloaded.exit_code == 0
    assert reloaded.command == "pip install --quiet pytest && pytest"
    assert "1 passed" in (reloaded.output or "")


async def test_run_tests_reports_a_real_failing_suite(
    monkeypatch: pytest.MonkeyPatch, test_run: TestRun
) -> None:
    async def failing_workspace(*_args: object, **_kwargs: object) -> Workspace:
        ws = await _make_local_workspace()
        (ws.root / "test_math.py").write_text("def test_addition():\n    assert 1 + 1 == 3\n")
        return ws

    monkeypatch.setattr(
        test_runner_module, "get_access_token", AsyncMock(return_value="dummy-token")
    )
    monkeypatch.setattr(test_runner_module, "create_workspace", failing_workspace)
    # Otherwise a failing run now triggers the Milestone 8 fix loop, which
    # would make a real, unmocked LLM call -- this test is specifically
    # about one real failing sandbox run being captured correctly, not the
    # fix loop (see the dedicated fix-loop tests below).
    monkeypatch.setattr(test_runner_module, "MAX_FIX_ATTEMPTS", 0)

    async with async_session_factory() as db:
        db_test_run = await db.get(
            TestRun,
            test_run.id,
            options=_TEST_RUN_LOAD_OPTIONS,
        )
        await run_tests(db, db_test_run)

    reloaded = await _reload(test_run)
    assert reloaded.status == "failed"
    assert reloaded.exit_code != 0
    assert "1 failed" in (reloaded.output or "")


async def test_run_tests_errors_when_no_test_command_detected(
    monkeypatch: pytest.MonkeyPatch, test_run: TestRun
) -> None:
    async def empty_workspace(*_args: object, **_kwargs: object) -> Workspace:
        root = Path(tempfile.mkdtemp(prefix="test-runner-empty-"))
        (root / "README.md").write_text("nothing to test here\n")
        ws = Workspace(root)
        await ws._run_git("init", "-q")
        await ws._run_git("config", "user.email", "test@localhost")
        await ws._run_git("config", "user.name", "Test")
        await ws._run_git("add", "-A")
        await ws._run_git("commit", "-q", "-m", "Initial state")
        return ws

    monkeypatch.setattr(
        test_runner_module, "get_access_token", AsyncMock(return_value="dummy-token")
    )
    monkeypatch.setattr(test_runner_module, "create_workspace", empty_workspace)

    async with async_session_factory() as db:
        db_test_run = await db.get(
            TestRun,
            test_run.id,
            options=_TEST_RUN_LOAD_OPTIONS,
        )
        await run_tests(db, db_test_run)

    reloaded = await _reload(test_run)
    assert reloaded.status == "error"
    assert "couldn't detect a test command" in (reloaded.output or "").lower()
    assert reloaded.command is None


async def test_run_tests_applies_the_stored_diff_before_running(
    monkeypatch: pytest.MonkeyPatch, test_run: TestRun
) -> None:
    """The diff must actually get applied -- a test that only passes
    because of the coder's edit proves the reconstructed workspace reflects
    those edits, not just the original unmodified repo."""
    _patch_workspace_creation(monkeypatch)

    # Build the real diff the same way the Coder agent's workspace would:
    # edit a file in a throwaway workspace, capture git_diff(), discard it.
    seed = await _make_local_workspace()
    seed.edit_file("test_math.py", "1 + 1 == 2", "1 + 1 == 2 and True")
    real_diff = await seed.git_diff()
    seed.cleanup()

    async with async_session_factory() as db:
        db_test_run = await db.get(
            TestRun,
            test_run.id,
            options=_TEST_RUN_LOAD_OPTIONS,
        )
        db_test_run.code_change.diff = real_diff
        await run_tests(db, db_test_run)

    reloaded = await _reload(test_run)
    assert reloaded.status == "passed"


async def test_run_tests_errors_when_the_diff_does_not_apply(
    monkeypatch: pytest.MonkeyPatch, test_run: TestRun
) -> None:
    _patch_workspace_creation(monkeypatch)
    bogus_diff = (
        "diff --git a/test_math.py b/test_math.py\n"
        "index 0000000..1111111 100644\n"
        "--- a/test_math.py\n"
        "+++ b/test_math.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def test_addition():\n"
        "-    assert 1 + 1 == 999999\n"
        "+    assert 1 + 1 == 2\n"
    )

    async with async_session_factory() as db:
        db_test_run = await db.get(
            TestRun,
            test_run.id,
            options=_TEST_RUN_LOAD_OPTIONS,
        )
        db_test_run.code_change.diff = bogus_diff
        await run_tests(db, db_test_run)

    reloaded = await _reload(test_run)
    assert reloaded.status == "error"
    assert "git apply" in (reloaded.output or "")


async def _failing_workspace(*_args: object, **_kwargs: object) -> Workspace:
    """Unlike _make_local_workspace, bakes the failing assertion into the
    *initial commit* itself rather than leaving it as an uncommitted change
    on top of a correct one -- a fix that reverts to genuinely-correct
    content must show up as a real diff against this workspace's own
    history, not coincidentally collapse to nothing because it happens to
    land back on the initial commit's already-correct content."""
    root = Path(tempfile.mkdtemp(prefix="test-runner-workspace-"))
    (root / "test_math.py").write_text("def test_addition():\n    assert 1 + 1 == 3\n")
    ws = Workspace(root)
    await ws._run_git("init", "-q")
    await ws._run_git("config", "user.email", "test@localhost")
    await ws._run_git("config", "user.name", "Test")
    await ws._run_git("add", "-A")
    await ws._run_git("commit", "-q", "-m", "Initial state")
    return ws


async def test_run_tests_fixes_a_failing_suite_and_passes(
    monkeypatch: pytest.MonkeyPatch, test_run: TestRun
) -> None:
    """The core Milestone 8 behavior: a failing run triggers a fix attempt
    in the same workspace, then re-runs the tests -- a fix that actually
    corrects the bug must be reflected in the next sandbox run and in the
    final status/diff/summary."""
    monkeypatch.setattr(
        test_runner_module, "get_access_token", AsyncMock(return_value="dummy-token")
    )
    monkeypatch.setattr(test_runner_module, "create_workspace", _failing_workspace)

    async def fake_fix(db, workspace, repository_id, issue, plan, code_change, test_run) -> str:
        workspace.edit_file("test_math.py", "1 + 1 == 3", "1 + 1 == 2")
        return "Fixed the wrong expected value."

    fix_spy = AsyncMock(side_effect=fake_fix)
    monkeypatch.setattr(test_runner_module, "fix_code_change", fix_spy)

    async with async_session_factory() as db:
        db_test_run = await db.get(
            TestRun,
            test_run.id,
            options=_TEST_RUN_LOAD_OPTIONS,
        )
        await run_tests(db, db_test_run)

    fix_spy.assert_awaited_once()
    reloaded = await _reload(test_run)
    assert reloaded.status == "passed"
    assert reloaded.fix_attempts == 1
    assert "1 passed" in (reloaded.output or "")

    async with async_session_factory() as db:
        code_change = await db.get(CodeChange, reloaded.code_change_id)
        assert code_change is not None
        assert code_change.summary == "Fixed the wrong expected value."
        assert "1 + 1 == 2" in (code_change.diff or "")


async def test_run_tests_exhausts_fix_attempts_and_stays_failed(
    monkeypatch: pytest.MonkeyPatch, test_run: TestRun
) -> None:
    """A "fix" that never actually fixes anything must not loop forever --
    proves the loop is bounded by MAX_FIX_ATTEMPTS, and the last real test
    verdict ("failed") is what's left standing, not something else."""
    monkeypatch.setattr(
        test_runner_module, "get_access_token", AsyncMock(return_value="dummy-token")
    )
    monkeypatch.setattr(test_runner_module, "create_workspace", _failing_workspace)
    fix_spy = AsyncMock(return_value="Tried something, still broken.")
    monkeypatch.setattr(test_runner_module, "fix_code_change", fix_spy)

    async with async_session_factory() as db:
        db_test_run = await db.get(
            TestRun,
            test_run.id,
            options=_TEST_RUN_LOAD_OPTIONS,
        )
        await run_tests(db, db_test_run)

    assert fix_spy.await_count == test_runner_module.MAX_FIX_ATTEMPTS
    reloaded = await _reload(test_run)
    assert reloaded.status == "failed"
    assert reloaded.fix_attempts == test_runner_module.MAX_FIX_ATTEMPTS


async def test_run_tests_does_not_attempt_a_fix_when_status_is_error(
    monkeypatch: pytest.MonkeyPatch, test_run: TestRun
) -> None:
    """error means no verdict was reached at all -- the fix loop must not
    fire for it, only for a genuine "failed" (see TestRun's docstring)."""

    async def empty_workspace(*_args: object, **_kwargs: object) -> Workspace:
        root = Path(tempfile.mkdtemp(prefix="test-runner-empty-"))
        (root / "README.md").write_text("nothing to test here\n")
        ws = Workspace(root)
        await ws._run_git("init", "-q")
        await ws._run_git("config", "user.email", "test@localhost")
        await ws._run_git("config", "user.name", "Test")
        await ws._run_git("add", "-A")
        await ws._run_git("commit", "-q", "-m", "Initial state")
        return ws

    monkeypatch.setattr(
        test_runner_module, "get_access_token", AsyncMock(return_value="dummy-token")
    )
    monkeypatch.setattr(test_runner_module, "create_workspace", empty_workspace)
    fix_spy = AsyncMock()
    monkeypatch.setattr(test_runner_module, "fix_code_change", fix_spy)

    async with async_session_factory() as db:
        db_test_run = await db.get(
            TestRun,
            test_run.id,
            options=_TEST_RUN_LOAD_OPTIONS,
        )
        await run_tests(db, db_test_run)

    fix_spy.assert_not_awaited()
    reloaded = await _reload(test_run)
    assert reloaded.status == "error"
    assert reloaded.fix_attempts == 0


async def test_run_tests_stays_failed_when_a_fix_attempt_itself_raises(
    monkeypatch: pytest.MonkeyPatch, test_run: TestRun
) -> None:
    """A fix attempt blowing up (an LLM/provider error, etc.) is not the
    same as the *tests* erroring -- the last real "failed" verdict must
    survive, not get silently overwritten by "error" (see run_tests's
    docstring for why that distinction matters)."""
    monkeypatch.setattr(
        test_runner_module, "get_access_token", AsyncMock(return_value="dummy-token")
    )
    monkeypatch.setattr(test_runner_module, "create_workspace", _failing_workspace)
    monkeypatch.setattr(
        test_runner_module, "fix_code_change", AsyncMock(side_effect=RuntimeError("LLM blew up"))
    )

    async with async_session_factory() as db:
        db_test_run = await db.get(
            TestRun,
            test_run.id,
            options=_TEST_RUN_LOAD_OPTIONS,
        )
        await run_tests(db, db_test_run)

    reloaded = await _reload(test_run)
    assert reloaded.status == "failed"
    assert reloaded.fix_attempts == 1
    assert "1 failed" in (reloaded.output or "")
    assert "Fix attempt 1" in (reloaded.output or "")
