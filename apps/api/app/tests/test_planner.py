import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from app.agents import planner as planner_module
from app.agents.planner import FORCE_SUBMIT_ATTEMPTS, MAX_TURNS, create_plan
from app.core.db import async_session_factory
from app.llm.provider import LLMResponse, ToolCall
from app.models.issue import Issue
from app.models.plan import Plan
from app.models.repository import Repository
from app.models.user import User


@pytest.fixture
async def issue() -> AsyncIterator[Issue]:
    async with async_session_factory() as db:
        user = User(username=f"planner-test-{uuid.uuid4().hex[:8]}")
        db.add(user)
        await db.flush()

        repo = Repository(
            owner_id=user.id, github_id=-1, full_name="planner-test/repo", default_branch="main"
        )
        db.add(repo)
        await db.flush()

        created_issue = Issue(
            repository_id=repo.id,
            created_by_id=user.id,
            description="Fix the login bug",
            planning_status="queued",
        )
        db.add(created_issue)
        await db.commit()
        await db.refresh(created_issue)

        try:
            yield created_issue
        finally:
            async with async_session_factory() as cleanup_db:
                await cleanup_db.execute(delete(Plan).where(Plan.issue_id == created_issue.id))
                await cleanup_db.execute(delete(Issue).where(Issue.id == created_issue.id))
                await cleanup_db.execute(delete(Repository).where(Repository.id == repo.id))
                await cleanup_db.execute(delete(User).where(User.id == user.id))
                await cleanup_db.commit()


def _submit_plan_call(**overrides: object) -> ToolCall:
    arguments = {
        "summary": "Fix the null check in the login handler",
        "relevant_files": [{"file_path": "auth/login.py", "reason": "handles login"}],
        "implementation_steps": ["Add a null check", "Add a test"],
        "tests_to_add_or_change": ["test_login_with_empty_email"],
        "risks": ["Could affect other auth flows"],
        **overrides,
    }
    return ToolCall(id="call_submit", name="submit_plan", arguments=arguments)


async def _reload(issue: Issue) -> Issue:
    async with async_session_factory() as db:
        result = await db.execute(select(Issue).where(Issue.id == issue.id))
        return result.scalar_one()


async def _reload_plan(issue: Issue) -> Plan | None:
    async with async_session_factory() as db:
        result = await db.execute(select(Plan).where(Plan.issue_id == issue.id))
        return result.scalar_one_or_none()


async def test_create_plan_submits_directly(monkeypatch: pytest.MonkeyPatch, issue: Issue) -> None:
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(content=None, tool_calls=[_submit_plan_call()])
    )
    monkeypatch.setattr(planner_module, "get_llm_provider", lambda _name: fake_provider)

    async with async_session_factory() as db:
        db_issue = await db.get(Issue, issue.id)
        await create_plan(db, db_issue)

    reloaded = await _reload(issue)
    assert reloaded.planning_status == "planned"
    assert reloaded.planning_error is None

    plan = await _reload_plan(issue)
    assert plan is not None
    assert plan.summary == "Fix the null check in the login handler"
    assert plan.relevant_files == [{"file_path": "auth/login.py", "reason": "handles login"}]


async def test_create_plan_executes_search_code_before_submitting(
    monkeypatch: pytest.MonkeyPatch, issue: Issue
) -> None:
    search_call = ToolCall(id="call_search", name="search_code", arguments={"query": "login"})
    responses = [
        LLMResponse(content=None, tool_calls=[search_call]),
        LLMResponse(content=None, tool_calls=[_submit_plan_call()]),
    ]
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(side_effect=responses)
    monkeypatch.setattr(planner_module, "get_llm_provider", lambda _name: fake_provider)

    fake_search = AsyncMock(return_value=[])
    monkeypatch.setattr(planner_module, "search_code", fake_search)

    async with async_session_factory() as db:
        db_issue = await db.get(Issue, issue.id)
        await create_plan(db, db_issue)

    fake_search.assert_awaited_once()
    call_args = fake_search.call_args
    assert call_args.args[1] == issue.repository_id
    assert call_args.args[2] == "login"

    # The tool result must actually be threaded back into the conversation,
    # not just executed and discarded -- the second complete() call's
    # message history should contain a "tool" role message.
    second_call_messages = fake_provider.complete.call_args_list[1].args[0]
    assert any(m.role == "tool" and m.tool_call_id == "call_search" for m in second_call_messages)

    reloaded = await _reload(issue)
    assert reloaded.planning_status == "planned"


async def test_create_plan_handles_malformed_submit_plan_arguments(
    monkeypatch: pytest.MonkeyPatch, issue: Issue
) -> None:
    bad_call = _submit_plan_call(relevant_files="not a list")
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(content=None, tool_calls=[bad_call])
    )
    monkeypatch.setattr(planner_module, "get_llm_provider", lambda _name: fake_provider)

    async with async_session_factory() as db:
        db_issue = await db.get(Issue, issue.id)
        await create_plan(db, db_issue)

    reloaded = await _reload(issue)
    assert reloaded.planning_status == "failed"
    assert reloaded.planning_error is not None
    assert await _reload_plan(issue) is None


async def test_create_plan_fails_after_max_turns_without_submit(
    monkeypatch: pytest.MonkeyPatch, issue: Issue
) -> None:
    # Never calls submit_plan -- just keeps talking. Must not loop forever.
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(content="Still thinking...", tool_calls=[])
    )
    monkeypatch.setattr(planner_module, "get_llm_provider", lambda _name: fake_provider)

    async with async_session_factory() as db:
        db_issue = await db.get(Issue, issue.id)
        await create_plan(db, db_issue)

    assert fake_provider.complete.await_count == MAX_TURNS + FORCE_SUBMIT_ATTEMPTS
    reloaded = await _reload(issue)
    assert reloaded.planning_status == "failed"
    assert "did not submit a plan" in (reloaded.planning_error or "")


async def test_create_plan_forces_submission_after_free_choice_exhausted(
    monkeypatch: pytest.MonkeyPatch, issue: Issue
) -> None:
    """A model that would otherwise keep searching forever (see the real
    Groq behavior this is modeling) must still be forced to conclude with a
    real plan once the free-choice phase runs out, rather than exhausting
    MAX_TURNS and failing outright -- see the forced-retry phase in
    create_plan, after the main `for _turn in range(MAX_TURNS)` loop."""
    search_call = ToolCall(id="call_search", name="search_code", arguments={"query": "x"})

    async def keeps_searching_unless_forced(
        _messages: object,
        *,
        tools: tuple[object, ...] = (),
        force_tool: str | None = None,
        **_kwargs: object,
    ) -> LLMResponse:
        if force_tool == "submit_plan":
            # Regression check: search_code must not even be offered on a
            # forced attempt, not just discouraged -- a model that can't see
            # it can't attempt it (see the comment in create_plan).
            assert all(t.name != "search_code" for t in tools)
            return LLMResponse(content=None, tool_calls=[_submit_plan_call()])
        return LLMResponse(content=None, tool_calls=[search_call])

    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(side_effect=keeps_searching_unless_forced)
    monkeypatch.setattr(planner_module, "get_llm_provider", lambda _name: fake_provider)
    monkeypatch.setattr(planner_module, "search_code", AsyncMock(return_value=[]))

    async with async_session_factory() as db:
        db_issue = await db.get(Issue, issue.id)
        await create_plan(db, db_issue)

    # MAX_TURNS free-choice turns (always search_code, never forced) plus
    # exactly one forced attempt (which this fake immediately complies with).
    assert fake_provider.complete.await_count == MAX_TURNS + 1
    last_call_kwargs = fake_provider.complete.call_args_list[-1].kwargs
    assert last_call_kwargs["force_tool"] == "submit_plan"

    reloaded = await _reload(issue)
    assert reloaded.planning_status == "planned"
    plan = await _reload_plan(issue)
    assert plan is not None


async def test_create_plan_retries_a_forced_attempt_that_fails(
    monkeypatch: pytest.MonkeyPatch, issue: Issue
) -> None:
    """Regression test for real Groq behavior: even a forced, narrowed-schema
    attempt doesn't always land on the first try. The forced-retry phase
    must retry rather than giving up after a single failed attempt."""
    responses = (
        # MAX_TURNS free-choice turns, never submitting.
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="s", name="search_code", arguments={"query": "x"})],
            )
        ]
        * MAX_TURNS
        # First forced attempt: model answers in plain text, ignoring the
        # forced tool_choice entirely (observed in practice).
        + [LLMResponse(content="I'm not sure yet.", tool_calls=[])]
        # Second forced attempt succeeds.
        + [LLMResponse(content=None, tool_calls=[_submit_plan_call()])]
    )
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(side_effect=responses)
    monkeypatch.setattr(planner_module, "get_llm_provider", lambda _name: fake_provider)
    monkeypatch.setattr(planner_module, "search_code", AsyncMock(return_value=[]))

    async with async_session_factory() as db:
        db_issue = await db.get(Issue, issue.id)
        await create_plan(db, db_issue)

    assert fake_provider.complete.await_count == MAX_TURNS + 2
    reloaded = await _reload(issue)
    assert reloaded.planning_status == "planned"
