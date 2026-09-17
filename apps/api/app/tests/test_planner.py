import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from app.agents import planner as planner_module
from app.agents.planner import (
    FORCE_SUBMIT_ATTEMPTS,
    MAX_ESTIMATED_TOKENS_BEFORE_FORCING_SUBMIT,
    MAX_TURNS,
    create_plan,
)
from app.agents.tools import MAX_SEARCH_RESULT_CONTENT_CHARS, SEARCH_RESULT_LIMIT
from app.core.db import async_session_factory
from app.llm.provider import LLMResponse, TokenUsage, ToolCall, ToolSpec
from app.models.issue import Issue
from app.models.plan import Plan
from app.models.repository import Repository
from app.models.usage_record import UsageRecord
from app.models.user import User
from app.services.search import SearchResult


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
        assert db_issue is not None
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
        assert db_issue is not None
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
        assert db_issue is not None
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
        assert db_issue is not None
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
        tools: tuple[ToolSpec, ...] = (),
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
        assert db_issue is not None
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
        assert db_issue is not None
        await create_plan(db, db_issue)

    assert fake_provider.complete.await_count == MAX_TURNS + 2
    reloaded = await _reload(issue)
    assert reloaded.planning_status == "planned"


async def test_create_plan_records_token_usage(
    monkeypatch: pytest.MonkeyPatch, issue: Issue
) -> None:
    """Milestone 11: every real provider.complete() call must be recorded
    for cost tracking (see app/services/usage.py), tagged with this
    issue's own repository -- not just discarded once the response is
    read."""
    fake_provider = AsyncMock()
    fake_provider.model = "test-model"
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(
            content=None,
            tool_calls=[_submit_plan_call()],
            usage=TokenUsage(input_tokens=100, output_tokens=50),
        )
    )
    monkeypatch.setattr(planner_module, "get_llm_provider", lambda _name: fake_provider)

    async with async_session_factory() as db:
        db_issue = await db.get(Issue, issue.id)
        assert db_issue is not None
        await create_plan(db, db_issue)

    async with async_session_factory() as db:
        result = await db.execute(
            select(UsageRecord).where(UsageRecord.issue_id == issue.id)
        )
        records = list(result.scalars().all())

    assert len(records) == 1
    assert records[0].agent == "planner"
    assert records[0].model == "test-model"
    assert records[0].input_tokens == 100
    assert records[0].output_tokens == 50
    assert records[0].repository_id == issue.repository_id


def _make_large_search_results() -> list[SearchResult]:
    """SEARCH_RESULT_LIMIT results each at the per-result cap -- the
    worst-case content volume a single search_code call can legitimately
    return post-truncation, matching what a real, content-rich repository
    produces (see MAX_SEARCH_RESULT_CONTENT_CHARS's own comment for the
    live production 413 this whole mechanism exists to prevent)."""
    return [
        SearchResult(
            chunk_id=uuid.uuid4(),
            file_path=f"src/module_{i}.py",
            language="python",
            chunk_type="class",
            symbol_name=f"Thing{i}",
            start_line=1,
            end_line=200,
            content="x" * MAX_SEARCH_RESULT_CONTENT_CHARS,
            score=0.9,
        )
        for i in range(SEARCH_RESULT_LIMIT)
    ]


async def test_create_plan_stops_offering_search_code_once_conversation_is_large(
    monkeypatch: pytest.MonkeyPatch, issue: Issue
) -> None:
    """Regression test for a real production bug: a model that keeps
    calling search_code against a content-rich repository can grow the
    conversation past a real provider's per-request token limit (a live
    413 from Groq's 8000 TPM free-tier limit, not a hypothetical -- see
    MAX_ESTIMATED_TOKENS_BEFORE_FORCING_SUBMIT's own comment). This
    provider always returns another search_code call, never submit_plan on
    its own -- if nothing stopped it, it would keep going until MAX_TURNS.
    It must instead be cut off by the size safeguard well before then, and
    the call that cuts if off must not offer search_code as an option."""
    search_call = ToolCall(id="call_search", name="search_code", arguments={"query": "x"})
    # More than enough canned "keep searching" responses to prove the
    # safeguard -- not turn count -- is what stops it; force-submit's own
    # responses (once search_code is no longer offered) also keep
    # "searching" to prove *that* call correctly can't, since search_code
    # isn't in its own tools list either way.
    fake_provider = AsyncMock()
    fake_provider.complete = AsyncMock(
        return_value=LLMResponse(content=None, tool_calls=[search_call])
    )
    monkeypatch.setattr(planner_module, "get_llm_provider", lambda _name: fake_provider)
    monkeypatch.setattr(
        planner_module, "search_code", AsyncMock(return_value=_make_large_search_results())
    )

    async with async_session_factory() as db:
        db_issue = await db.get(Issue, issue.id)
        assert db_issue is not None
        await create_plan(db, db_issue)

    reloaded = await _reload(issue)
    assert reloaded.planning_status == "failed"  # never submitted -- this provider never does
    assert "did not submit a plan" in (reloaded.planning_error or "")

    # The real assertion: search_code must stop being offered well before
    # MAX_TURNS + FORCE_SUBMIT_ATTEMPTS calls were made -- proving the size
    # safeguard (not the turn-count cap) is what actually stopped it.
    calls_with_search_code_offered = [
        c
        for c in fake_provider.complete.call_args_list
        if any(t.name == "search_code" for t in c.kwargs["tools"])
    ]
    assert len(calls_with_search_code_offered) < MAX_TURNS
    # Every call once cut off (including every force-submit attempt) must
    # never re-offer search_code.
    calls_after_cutoff = fake_provider.complete.call_args_list[len(calls_with_search_code_offered):]
    assert len(calls_after_cutoff) > 0
    for c in calls_after_cutoff:
        assert all(t.name != "search_code" for t in c.kwargs["tools"])

    # The cutoff fired because real content accumulated past the
    # threshold, not because it fired instantly on turn one.
    assert len(calls_with_search_code_offered) >= 1
    accumulated_content_chars = (
        len(calls_with_search_code_offered) * SEARCH_RESULT_LIMIT * MAX_SEARCH_RESULT_CONTENT_CHARS
    )
    assert accumulated_content_chars >= MAX_ESTIMATED_TOKENS_BEFORE_FORCING_SUBMIT
