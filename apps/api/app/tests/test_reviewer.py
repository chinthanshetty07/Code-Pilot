import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.agents import reviewer as reviewer_module
from app.agents.reviewer import FORCE_SUBMIT_ATTEMPTS, MAX_TURNS, create_review
from app.core.db import async_session_factory
from app.llm.provider import LLMResponse, ToolCall
from app.models.code_change import CodeChange
from app.models.issue import Issue
from app.models.plan import Plan
from app.models.repository import Repository
from app.models.review import Review
from app.models.test_run import TestRun
from app.models.user import User
from app.services.workspace import Workspace


async def _make_local_workspace() -> Workspace:
    root = Path(tempfile.mkdtemp(prefix="reviewer-test-workspace-"))
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text(
        "def greet(name):\n    return f'hello {name.capitalize()}'\n"
    )
    ws = Workspace(root)
    await ws._run_git("init", "-q")
    await ws._run_git("config", "user.email", "test@localhost")
    await ws._run_git("config", "user.name", "Test")
    await ws._run_git("add", "-A")
    await ws._run_git("commit", "-q", "-m", "Initial state")
    return ws


@pytest.fixture
async def review() -> AsyncIterator[Review]:
    async with async_session_factory() as db:
        user = User(username=f"reviewer-test-{uuid.uuid4().hex[:8]}")
        db.add(user)
        await db.flush()

        repo = Repository(
            owner_id=user.id, github_id=-1, full_name="reviewer-test/repo", default_branch="main"
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

        code_change = CodeChange(
            issue_id=issue.id,
            generation_status="generated",
            summary="Capitalized the name in greet().",
            # Empty rather than a placeholder string: create_review calls
            # workspace.apply_diff() whenever this is non-empty, and a
            # placeholder isn't a valid patch -- git apply would fail on
            # every test in this file. These tests are about the agent
            # loop's mechanics, not diff application (already covered by
            # test_workspace.py/test_test_runner.py), so the workspace
            # just starts at _make_local_workspace's own initial content.
            diff="",
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

        created = Review(code_change_id=code_change.id, status="queued")
        db.add(created)
        await db.commit()

        try:
            async with async_session_factory() as fresh_db:
                result = await fresh_db.execute(
                    select(Review)
                    .options(
                        selectinload(Review.code_change)
                        .selectinload(CodeChange.issue)
                        .selectinload(Issue.repository),
                        selectinload(Review.code_change)
                        .selectinload(CodeChange.issue)
                        .selectinload(Issue.plan),
                        selectinload(Review.code_change).selectinload(CodeChange.test_run),
                    )
                    .where(Review.id == created.id)
                )
                yield result.scalar_one()
        finally:
            async with async_session_factory() as cleanup_db:
                await cleanup_db.execute(
                    delete(Review).where(Review.code_change_id == code_change.id)
                )
                await cleanup_db.execute(
                    delete(TestRun).where(TestRun.code_change_id == code_change.id)
                )
                await cleanup_db.execute(delete(CodeChange).where(CodeChange.issue_id == issue.id))
                await cleanup_db.execute(delete(Plan).where(Plan.issue_id == issue.id))
                await cleanup_db.execute(delete(Issue).where(Issue.id == issue.id))
                await cleanup_db.execute(delete(Repository).where(Repository.id == repo.id))
                await cleanup_db.execute(delete(User).where(User.id == user.id))
                await cleanup_db.commit()


def _submit_call(**overrides: object) -> ToolCall:
    return ToolCall(
        id="call_submit",
        name="submit_review",
        arguments={"status": "approved", "summary": "Looks good.", "comments": [], **overrides},
    )


async def _fake_create_workspace(*_args: object, **_kwargs: object) -> Workspace:
    return await _make_local_workspace()


def _patch_reviewer(monkeypatch: pytest.MonkeyPatch, fake_provider) -> None:
    monkeypatch.setattr(reviewer_module, "get_llm_provider", lambda _name: fake_provider)
    monkeypatch.setattr(reviewer_module, "get_access_token", AsyncMock(return_value="dummy-token"))
    monkeypatch.setattr(reviewer_module, "create_workspace", _fake_create_workspace)


async def _reload(review: Review) -> Review:
    async with async_session_factory() as db:
        result = await db.execute(select(Review).where(Review.id == review.id))
        return result.scalar_one()


async def test_create_review_approves_clean_code(
    monkeypatch: pytest.MonkeyPatch, review: Review
) -> None:
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(content=None, tool_calls=[_submit_call()])
    )
    _patch_reviewer(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_review = await db.get(
            Review,
            review.id,
            options=[
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.repository),
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.plan),
                selectinload(Review.code_change).selectinload(CodeChange.test_run),
            ],
        )
        await create_review(db, db_review)

    reloaded = await _reload(review)
    assert reloaded.status == "approved"
    assert reloaded.error is None
    assert reloaded.summary == "Looks good."
    assert reloaded.comments == []


async def test_create_review_requests_changes_with_comments(
    monkeypatch: pytest.MonkeyPatch, review: Review
) -> None:
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(
            content=None,
            tool_calls=[
                _submit_call(
                    status="changes_requested",
                    summary="Missing a test for the empty-string case.",
                    comments=[
                        {
                            "file_path": "src/app.py",
                            "severity": "blocking",
                            "comment": "greet('') will crash on .capitalize() -- add a guard.",
                        }
                    ],
                )
            ],
        )
    )
    _patch_reviewer(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_review = await db.get(
            Review,
            review.id,
            options=[
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.repository),
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.plan),
                selectinload(Review.code_change).selectinload(CodeChange.test_run),
            ],
        )
        await create_review(db, db_review)

    reloaded = await _reload(review)
    assert reloaded.status == "changes_requested"
    assert reloaded.comments is not None
    assert len(reloaded.comments) == 1
    assert reloaded.comments[0]["severity"] == "blocking"
    assert "capitalize" in reloaded.comments[0]["comment"]


async def test_create_review_uses_read_file_and_search_code_first(
    monkeypatch: pytest.MonkeyPatch, review: Review
) -> None:
    """Confirms both read-only tools actually dispatch and feed real
    results back to the model, the same recoverable-loop pattern proven
    for the Planner/Coder agents' own tools."""
    read_call = ToolCall(id="call_read", name="read_file", arguments={"path": "src/app.py"})
    search_call = ToolCall(
        id="call_search", name="search_code", arguments={"query": "greet function callers"}
    )
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        side_effect=[
            LLMResponse(content=None, tool_calls=[read_call]),
            LLMResponse(content=None, tool_calls=[search_call]),
            LLMResponse(content=None, tool_calls=[_submit_call()]),
        ]
    )
    _patch_reviewer(monkeypatch, fake_provider)
    monkeypatch.setattr(reviewer_module, "search_code", AsyncMock(return_value=[]))

    async with async_session_factory() as db:
        db_review = await db.get(
            Review,
            review.id,
            options=[
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.repository),
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.plan),
                selectinload(Review.code_change).selectinload(CodeChange.test_run),
            ],
        )
        await create_review(db, db_review)

    second_call_messages = fake_provider.complete.call_args_list[1].args[0]
    assert any(
        m.role == "tool" and m.tool_call_id == "call_read" and "def greet" in (m.content or "")
        for m in second_call_messages
    )
    reloaded = await _reload(review)
    assert reloaded.status == "approved"


async def test_create_review_recovers_from_a_bad_read_file(
    monkeypatch: pytest.MonkeyPatch, review: Review
) -> None:
    bad_read = ToolCall(id="call_bad_read", name="read_file", arguments={"path": "nope.py"})
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        side_effect=[
            LLMResponse(content=None, tool_calls=[bad_read]),
            LLMResponse(content=None, tool_calls=[_submit_call()]),
        ]
    )
    _patch_reviewer(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_review = await db.get(
            Review,
            review.id,
            options=[
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.repository),
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.plan),
                selectinload(Review.code_change).selectinload(CodeChange.test_run),
            ],
        )
        await create_review(db, db_review)

    second_call_messages = fake_provider.complete.call_args_list[1].args[0]
    assert any(
        m.role == "tool" and m.tool_call_id == "call_bad_read" and "Error" in (m.content or "")
        for m in second_call_messages
    )
    reloaded = await _reload(review)
    assert reloaded.status == "approved"


async def test_create_review_forces_submit_after_free_choice_exhausted(
    monkeypatch: pytest.MonkeyPatch, review: Review
) -> None:
    read_call = ToolCall(id="call_read", name="read_file", arguments={"path": "src/app.py"})

    async def keeps_reading_unless_forced(
        _messages: object,
        *,
        tools: tuple[object, ...] = (),
        force_tool: str | None = None,
        **_kwargs: object,
    ) -> LLMResponse:
        if force_tool == "submit_review":
            assert all(t.name == "submit_review" for t in tools)
            return LLMResponse(content=None, tool_calls=[_submit_call()])
        return LLMResponse(content=None, tool_calls=[read_call])

    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(side_effect=keeps_reading_unless_forced)
    _patch_reviewer(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_review = await db.get(
            Review,
            review.id,
            options=[
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.repository),
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.plan),
                selectinload(Review.code_change).selectinload(CodeChange.test_run),
            ],
        )
        await create_review(db, db_review)

    assert fake_provider.complete.await_count == MAX_TURNS + 1
    reloaded = await _reload(review)
    assert reloaded.status == "approved"


async def test_create_review_fails_after_exhausting_all_attempts(
    monkeypatch: pytest.MonkeyPatch, review: Review
) -> None:
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(content="thinking...", tool_calls=[])
    )
    _patch_reviewer(monkeypatch, fake_provider)

    async with async_session_factory() as db:
        db_review = await db.get(
            Review,
            review.id,
            options=[
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.repository),
                selectinload(Review.code_change)
                .selectinload(CodeChange.issue)
                .selectinload(Issue.plan),
                selectinload(Review.code_change).selectinload(CodeChange.test_run),
            ],
        )
        await create_review(db, db_review)

    assert fake_provider.complete.await_count == MAX_TURNS + FORCE_SUBMIT_ATTEMPTS
    reloaded = await _reload(review)
    assert reloaded.status == "failed"
    assert "did not submit a verdict" in (reloaded.error or "")
