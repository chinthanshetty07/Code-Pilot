import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select

from app.core.db import async_session_factory
from app.llm.provider import TokenUsage
from app.models.issue import Issue
from app.models.repository import Repository
from app.models.usage_record import UsageRecord
from app.models.user import User
from app.services.usage import (
    estimate_cost_usd,
    get_issue_usage_summary,
    get_repository_usage_summary,
    record_llm_usage,
)


def test_estimate_cost_usd_uses_the_priced_models_own_rate() -> None:
    # $0.15 input / $0.60 output per 1M tokens (see the pricing table's own
    # comment for where these numbers come from).
    cost = estimate_cost_usd("groq", "openai/gpt-oss-120b", input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(0.15)

    cost = estimate_cost_usd("groq", "openai/gpt-oss-120b", input_tokens=0, output_tokens=1_000_000)
    assert cost == pytest.approx(0.60)

    # $0.75 input / $3.75 output per 1M tokens.
    cost = estimate_cost_usd(
        "gemini", "gemini-3.6-flash", input_tokens=500_000, output_tokens=200_000
    )
    assert cost == pytest.approx(0.75 * 0.5 + 3.75 * 0.2)


def test_estimate_cost_usd_falls_back_for_an_unpriced_model() -> None:
    # Not in the pricing table -- must still return a number (the default
    # rate), never raise a KeyError.
    cost = estimate_cost_usd("some-new-provider", "some-new-model", 1_000_000, 1_000_000)
    assert cost > 0


@pytest.fixture
async def repo_and_issue() -> AsyncIterator[tuple[Repository, Issue]]:
    async with async_session_factory() as db:
        user = User(username=f"usage-test-{uuid.uuid4().hex[:8]}")
        db.add(user)
        await db.flush()

        repo = Repository(
            owner_id=user.id, github_id=-1, full_name="usage-test/repo", default_branch="main"
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
        await db.commit()
        await db.refresh(repo)
        await db.refresh(issue)

        try:
            yield repo, issue
        finally:
            async with async_session_factory() as cleanup_db:
                await cleanup_db.execute(delete(Issue).where(Issue.id == issue.id))
                await cleanup_db.execute(delete(Repository).where(Repository.id == repo.id))
                await cleanup_db.execute(delete(User).where(User.id == user.id))
                await cleanup_db.commit()


async def test_record_llm_usage_writes_a_row(
    repo_and_issue: tuple[Repository, Issue],
) -> None:
    repo, issue = repo_and_issue
    async with async_session_factory() as db:
        await record_llm_usage(
            db,
            agent="planner",
            provider="groq",
            model="openai/gpt-oss-120b",
            usage=TokenUsage(input_tokens=123, output_tokens=45),
            repository_id=repo.id,
            issue_id=issue.id,
        )

    async with async_session_factory() as db:
        result = await db.execute(select(UsageRecord).where(UsageRecord.issue_id == issue.id))
        records = list(result.scalars().all())

    assert len(records) == 1
    assert records[0].input_tokens == 123
    assert records[0].output_tokens == 45


async def test_record_llm_usage_skips_a_none_usage_silently(
    repo_and_issue: tuple[Repository, Issue],
) -> None:
    """Never happens for a real Groq/Gemini response in practice (see
    LLMResponse.usage's docstring), but every agent calls this
    unconditionally -- must not raise, and must not record a misleading
    all-zero row."""
    repo, issue = repo_and_issue
    async with async_session_factory() as db:
        await record_llm_usage(
            db,
            agent="planner",
            provider="groq",
            model="openai/gpt-oss-120b",
            usage=None,
            repository_id=repo.id,
            issue_id=issue.id,
        )

    async with async_session_factory() as db:
        result = await db.execute(select(UsageRecord).where(UsageRecord.issue_id == issue.id))
        assert list(result.scalars().all()) == []


async def test_get_issue_usage_summary_aggregates_by_agent(
    repo_and_issue: tuple[Repository, Issue],
) -> None:
    repo, issue = repo_and_issue
    async with async_session_factory() as db:
        # Two planner calls (one turn, one forced retry) and one coder call.
        await record_llm_usage(
            db,
            agent="planner",
            provider="groq",
            model="openai/gpt-oss-120b",
            usage=TokenUsage(input_tokens=1000, output_tokens=100),
            repository_id=repo.id,
            issue_id=issue.id,
        )
        await record_llm_usage(
            db,
            agent="planner",
            provider="groq",
            model="openai/gpt-oss-120b",
            usage=TokenUsage(input_tokens=500, output_tokens=50),
            repository_id=repo.id,
            issue_id=issue.id,
        )
        await record_llm_usage(
            db,
            agent="coder",
            provider="gemini",
            model="gemini-3.6-flash",
            usage=TokenUsage(input_tokens=2000, output_tokens=300),
            repository_id=repo.id,
            issue_id=issue.id,
        )

    async with async_session_factory() as db:
        summary = await get_issue_usage_summary(db, issue.id)

    assert summary.total_input_tokens == 3500
    assert summary.total_output_tokens == 450
    assert len(summary.by_agent) == 2

    by_agent = {a.agent: a for a in summary.by_agent}
    assert by_agent["planner"].input_tokens == 1500
    assert by_agent["planner"].output_tokens == 150
    assert by_agent["planner"].estimated_cost_usd == pytest.approx(
        estimate_cost_usd("groq", "openai/gpt-oss-120b", 1500, 150)
    )
    assert by_agent["coder"].input_tokens == 2000
    assert by_agent["coder"].estimated_cost_usd == pytest.approx(
        estimate_cost_usd("gemini", "gemini-3.6-flash", 2000, 300)
    )
    assert summary.total_estimated_cost_usd == pytest.approx(
        by_agent["planner"].estimated_cost_usd + by_agent["coder"].estimated_cost_usd
    )


async def test_get_repository_usage_summary_is_scoped_to_the_repository(
    repo_and_issue: tuple[Repository, Issue],
) -> None:
    """A second, unrelated repository's usage must not leak into this
    one's summary -- the whole point of scoping by repository_id."""
    repo, issue = repo_and_issue
    async with async_session_factory() as db:
        other_user = User(username=f"usage-test-other-{uuid.uuid4().hex[:8]}")
        db.add(other_user)
        await db.flush()
        other_repo = Repository(
            owner_id=other_user.id,
            github_id=-2,
            full_name="usage-test/other-repo",
            default_branch="main",
        )
        db.add(other_repo)
        await db.commit()
        await db.refresh(other_repo)

    try:
        async with async_session_factory() as db:
            await record_llm_usage(
                db,
                agent="planner",
                provider="groq",
                model="openai/gpt-oss-120b",
                usage=TokenUsage(input_tokens=1000, output_tokens=100),
                repository_id=repo.id,
                issue_id=issue.id,
            )
            await record_llm_usage(
                db,
                agent="planner",
                provider="groq",
                model="openai/gpt-oss-120b",
                usage=TokenUsage(input_tokens=99_999, output_tokens=99_999),
                repository_id=other_repo.id,
                issue_id=None,
            )

        async with async_session_factory() as db:
            summary = await get_repository_usage_summary(db, repo.id)

        assert summary.total_input_tokens == 1000
        assert summary.total_output_tokens == 100
    finally:
        async with async_session_factory() as cleanup_db:
            await cleanup_db.execute(
                delete(UsageRecord).where(UsageRecord.repository_id == other_repo.id)
            )
            await cleanup_db.execute(delete(Repository).where(Repository.id == other_repo.id))
            await cleanup_db.execute(delete(User).where(User.id == other_user.id))
            await cleanup_db.commit()
