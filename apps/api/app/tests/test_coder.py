import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from app.agents import coder as coder_module
from app.agents.coder import FORCE_FINISH_ATTEMPTS, MAX_TURNS, create_code_change, fix_code_change
from app.core.db import async_session_factory
from app.llm.provider import LLMResponse, TokenUsage, ToolCall
from app.models.code_change import CodeChange
from app.models.issue import Issue
from app.models.plan import Plan
from app.models.repository import Repository
from app.models.test_run import TestRun
from app.models.usage_record import UsageRecord
from app.models.user import User
from app.services.workspace import Workspace


async def _make_local_workspace() -> Workspace:
    root = Path(tempfile.mkdtemp(prefix="coder-test-workspace-"))
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def greet(name):\n    return f'hello {name}'\n")
    ws = Workspace(root)
    await ws._run_git("init", "-q")
    await ws._run_git("config", "user.email", "test@localhost")
    await ws._run_git("config", "user.name", "Test")
    await ws._run_git("add", "-A")
    await ws._run_git("commit", "-q", "-m", "Initial state")
    return ws


@pytest.fixture
async def code_change() -> AsyncIterator[CodeChange]:
    async with async_session_factory() as db:
        user = User(username=f"coder-test-{uuid.uuid4().hex[:8]}")
        db.add(user)
        await db.flush()

        repo = Repository(
            owner_id=user.id, github_id=-1, full_name="coder-test/repo", default_branch="main"
        )
        db.add(repo)
        await db.flush()

        issue = Issue(
            repository_id=repo.id,
            created_by_id=user.id,
            description="The greet function should capitalize the name.",
            planning_status="planned",
        )
        db.add(issue)
        await db.flush()

        plan = Plan(
            issue_id=issue.id,
            summary="Capitalize the name in greet().",
            relevant_files=[{"file_path": "src/app.py", "reason": "contains greet()"}],
            implementation_steps=["Capitalize `name` before formatting it into the greeting"],
            tests_to_add_or_change=["test_greet_capitalizes_name"],
            risks=[],
        )
        db.add(plan)
        await db.flush()

        created = CodeChange(issue_id=issue.id, generation_status="queued")
        db.add(created)
        await db.commit()

        try:
            async with async_session_factory() as fresh_db:
                result = await fresh_db.execute(
                    select(CodeChange).where(CodeChange.id == created.id)
                )
                yield result.scalar_one()
        finally:
            async with async_session_factory() as cleanup_db:
                await cleanup_db.execute(delete(CodeChange).where(CodeChange.issue_id == issue.id))
                await cleanup_db.execute(delete(Plan).where(Plan.issue_id == issue.id))
                await cleanup_db.execute(delete(Issue).where(Issue.id == issue.id))
                await cleanup_db.execute(delete(Repository).where(Repository.id == repo.id))
                await cleanup_db.execute(delete(User).where(User.id == user.id))
                await cleanup_db.commit()


def _finish_call(**overrides: object) -> ToolCall:
    return ToolCall(id="call_finish", name="finish", arguments={"summary": "Done", **overrides})


async def _fake_create_workspace(*_args: object, **_kwargs: object) -> Workspace:
    return await _make_local_workspace()


def _patch_coder(monkeypatch: pytest.MonkeyPatch, fake_provider) -> None:
    monkeypatch.setattr(coder_module, "get_llm_provider", lambda _name: fake_provider)
    monkeypatch.setattr(coder_module, "get_access_token", AsyncMock(return_value="dummy-token"))
    monkeypatch.setattr(coder_module, "create_workspace", _fake_create_workspace)


async def _reload(code_change: CodeChange) -> CodeChange:
    async with async_session_factory() as db:
        result = await db.execute(select(CodeChange).where(CodeChange.id == code_change.id))
        return result.scalar_one()


async def _load_full(db, code_change_id: uuid.UUID) -> CodeChange:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(CodeChange)
        .options(
            selectinload(CodeChange.issue).selectinload(Issue.plan),
            selectinload(CodeChange.issue).selectinload(Issue.repository),
        )
        .where(CodeChange.id == code_change_id)
    )
    return result.scalar_one()


async def test_create_code_change_edits_a_file_then_finishes(
    monkeypatch: pytest.MonkeyPatch, code_change: CodeChange
) -> None:
    edit_call = ToolCall(
        id="call_edit",
        name="edit_file",
        arguments={
            "path": "src/app.py",
            "old_string": "hello {name}",
            "new_string": "hello {name.capitalize()}",
        },
    )
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        side_effect=[
            LLMResponse(content=None, tool_calls=[edit_call]),
            LLMResponse(content=None, tool_calls=[_finish_call()]),
        ]
    )
    _patch_coder(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_code_change = await _load_full(db, code_change.id)
        await create_code_change(db, db_code_change)

    reloaded = await _reload(code_change)
    assert reloaded.generation_status == "generated"
    assert reloaded.generation_error is None
    assert reloaded.summary == "Done"
    assert reloaded.diff is not None
    assert "src/app.py" in reloaded.diff
    assert "capitalize" in reloaded.diff


async def test_create_code_change_requires_a_plan(
    monkeypatch: pytest.MonkeyPatch, code_change: CodeChange
) -> None:
    fake_provider = AsyncMock()
    _patch_coder(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_code_change = await _load_full(db, code_change.id)
        db_code_change.issue.plan = None  # simulate an issue with no plan
        await create_code_change(db, db_code_change)

    fake_provider.complete.assert_not_awaited()
    reloaded = await _reload(code_change)
    assert reloaded.generation_status == "failed"
    assert "no plan" in (reloaded.generation_error or "").lower()


async def test_create_code_change_fails_when_nothing_changed(
    monkeypatch: pytest.MonkeyPatch, code_change: CodeChange
) -> None:
    # Finishes immediately without ever calling edit_file/create_file.
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(content=None, tool_calls=[_finish_call()])
    )
    _patch_coder(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_code_change = await _load_full(db, code_change.id)
        await create_code_change(db, db_code_change)

    reloaded = await _reload(code_change)
    assert reloaded.generation_status == "failed"
    assert "without making any file changes" in (reloaded.generation_error or "")


async def test_create_code_change_recovers_from_a_bad_edit(
    monkeypatch: pytest.MonkeyPatch, code_change: CodeChange
) -> None:
    """A bad edit_file call (old_string not found) must feed the error back
    as a tool result, not crash the whole run -- same recoverable-failure
    pattern as the Planner's bad search_code handling."""
    bad_edit = ToolCall(
        id="call_bad_edit",
        name="edit_file",
        arguments={"path": "src/app.py", "old_string": "not in the file", "new_string": "x"},
    )
    good_edit = ToolCall(
        id="call_good_edit",
        name="edit_file",
        arguments={"path": "src/app.py", "old_string": "hello {name}", "new_string": "hi {name}"},
    )
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        side_effect=[
            LLMResponse(content=None, tool_calls=[bad_edit]),
            LLMResponse(content=None, tool_calls=[good_edit]),
            LLMResponse(content=None, tool_calls=[_finish_call()]),
        ]
    )
    _patch_coder(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_code_change = await _load_full(db, code_change.id)
        await create_code_change(db, db_code_change)

    second_call_messages = fake_provider.complete.call_args_list[1].args[0]
    assert any(
        m.role == "tool" and m.tool_call_id == "call_bad_edit" and "Error" in (m.content or "")
        for m in second_call_messages
    )
    reloaded = await _reload(code_change)
    assert reloaded.generation_status == "generated"


async def test_create_code_change_forces_finish_after_free_choice_exhausted(
    monkeypatch: pytest.MonkeyPatch, code_change: CodeChange
) -> None:
    read_call = ToolCall(id="call_read", name="read_file", arguments={"path": "src/app.py"})

    async def keeps_reading_unless_forced(
        _messages: object,
        *,
        tools: tuple[object, ...] = (),
        force_tool: str | None = None,
        **_kwargs: object,
    ) -> LLMResponse:
        if force_tool == "finish":
            assert all(t.name == "finish" for t in tools)
            return LLMResponse(content=None, tool_calls=[_finish_call()])
        return LLMResponse(content=None, tool_calls=[read_call])

    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(side_effect=keeps_reading_unless_forced)
    _patch_coder(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_code_change = await _load_full(db, code_change.id)
        await create_code_change(db, db_code_change)

    assert fake_provider.complete.await_count == MAX_TURNS + 1
    reloaded = await _reload(code_change)
    # No edits were ever made (only reads), so this still correctly reports
    # failure -- forcing finish guarantees an attempt, not that it succeeds.
    assert reloaded.generation_status == "failed"
    assert "without making any file changes" in (reloaded.generation_error or "")


async def test_create_code_change_fails_after_exhausting_all_attempts(
    monkeypatch: pytest.MonkeyPatch, code_change: CodeChange
) -> None:
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(content="thinking...", tool_calls=[])
    )
    _patch_coder(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_code_change = await _load_full(db, code_change.id)
        await create_code_change(db, db_code_change)

    assert fake_provider.complete.await_count == MAX_TURNS + FORCE_FINISH_ATTEMPTS
    reloaded = await _reload(code_change)
    assert reloaded.generation_status == "failed"
    assert "did not finish" in (reloaded.generation_error or "")


async def test_fix_code_change_edits_and_returns_summary(
    monkeypatch: pytest.MonkeyPatch, code_change: CodeChange
) -> None:
    """fix_code_change (Milestone 8) shares create_code_change's tool loop
    but is seeded with a previous summary + failing test output instead of
    a fresh issue+plan -- and, unlike create_code_change, doesn't persist
    anything itself (no db.commit, no touching code_change.diff/summary):
    that's app/services/test_runner.py's job, since it's the one that
    knows whether this is the first attempt or attempt N."""
    edit_call = ToolCall(
        id="call_edit",
        name="edit_file",
        arguments={
            "path": "src/app.py",
            "old_string": "hello {name}",
            "new_string": "hello {name.capitalize()}",
        },
    )
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        side_effect=[
            LLMResponse(content=None, tool_calls=[edit_call]),
            LLMResponse(
                content=None,
                tool_calls=[_finish_call(summary="Fixed the capitalization bug.")],
            ),
        ]
    )
    monkeypatch.setattr(coder_module, "get_llm_provider", lambda _name: fake_provider)

    workspace = await _make_local_workspace()
    try:
        async with async_session_factory() as db:
            db_code_change = await _load_full(db, code_change.id)
            test_run = TestRun(
                code_change_id=db_code_change.id,
                status="failed",
                command="pytest",
                output="AssertionError: expected 'Hello alice' but got 'hello alice'",
                exit_code=1,
            )
            summary = await fix_code_change(
                db,
                workspace,
                db_code_change.issue.repository.id,
                db_code_change.issue,
                db_code_change.issue.plan,
                db_code_change,
                test_run,
            )

        assert summary == "Fixed the capitalization bug."
        diff = await workspace.git_diff()
        assert "capitalize" in diff

        # Doesn't persist anything itself -- still exactly as loaded.
        reloaded = await _reload(code_change)
        assert reloaded.summary is None
        assert reloaded.diff is None
    finally:
        workspace.cleanup()

    first_call_messages = fake_provider.complete.call_args_list[0].args[0]
    assert any(
        "expected 'Hello alice'" in (m.content or "") for m in first_call_messages
    )


async def test_create_code_change_records_token_usage(
    monkeypatch: pytest.MonkeyPatch, code_change: CodeChange
) -> None:
    """Milestone 11: every real provider.complete() call must be recorded
    for cost tracking (see app/services/usage.py), tagged agent="coder"
    regardless of how many turns it took."""
    edit_call = ToolCall(
        id="call_edit",
        name="edit_file",
        arguments={
            "path": "src/app.py",
            "old_string": "hello {name}",
            "new_string": "hello {name.capitalize()}",
        },
    )
    fake_provider = AsyncMock()
    fake_provider.model = "test-model"
    fake_provider.complete = AsyncMock(
        side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[edit_call],
                usage=TokenUsage(input_tokens=200, output_tokens=40),
            ),
            LLMResponse(
                content=None,
                tool_calls=[_finish_call()],
                usage=TokenUsage(input_tokens=250, output_tokens=20),
            ),
        ]
    )
    _patch_coder(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_code_change = await _load_full(db, code_change.id)
        await create_code_change(db, db_code_change)

    async with async_session_factory() as db:
        result = await db.execute(
            select(UsageRecord).where(UsageRecord.issue_id == code_change.issue_id)
        )
        records = list(result.scalars().all())

    assert len(records) == 2
    assert all(r.agent == "coder" and r.model == "test-model" for r in records)
    assert sum(r.input_tokens for r in records) == 450
    assert sum(r.output_tokens for r in records) == 60
