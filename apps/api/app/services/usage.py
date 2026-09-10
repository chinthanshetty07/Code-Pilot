"""AI cost tracking (Milestone 11). Scope note: this covers the three
agent roles (Planner, Coder, Reviewer) only -- not embedding/indexing
usage. Checked empirically while building this: Gemini's embed_content
response exposes only a billable_character_count, not a token count
(unlike its generate_content responses, which do), so it isn't
comparable to the other providers' token-based numbers without a second,
different cost model. The per-issue "what did processing this cost"
story -- the one actually worth showing -- is fully served by the three
agent roles alone; embeddings are a smaller, one-time-per-repository cost
that would need its own, inconsistent unit to track accurately.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.provider import TokenUsage
from app.models.usage_record import UsageRecord

# USD per 1,000,000 tokens (input, output) -- verified against each
# provider's own current pricing docs while building this milestone, not
# assumed from training data (pricing drifts, and gemini-3.6-flash in
# particular is recent enough that stale knowledge is a real risk):
#   - Groq (console.groq.com/docs/models, "GPT OSS 120B" row):
#     $0.15 input / $0.60 output.
#   - Gemini (ai.google.dev/gemini-api/docs/pricing, gemini-3.6-flash):
#     $0.75 input / $3.75 output -- current tier, through 2026-12-31;
#     Google's own published schedule roughly doubles both after that.
#     This table intentionally tracks the CURRENT rate, not the future
#     one -- revisit if that date passes.
# This project's actual Groq/Gemini calls run on each provider's free
# tier, so nothing here is ever really billed -- these are paid-tier
# rates, used to produce a meaningful "what would this have cost"
# estimate rather than an always-$0 number that wouldn't demonstrate
# anything.
_PRICING_USD_PER_MILLION_TOKENS: dict[tuple[str, str], tuple[float, float]] = {
    ("groq", "openai/gpt-oss-120b"): (0.15, 0.60),
    ("gemini", "gemini-3.6-flash"): (0.75, 3.75),
}
# A (provider, model) combination not in the table above (e.g. a model
# swapped in later without updating this) falls back to this rather than
# silently reporting $0 -- a rough, clearly-labeled estimate is more
# honest than a wrong zero. Groq's own 120B rate specifically: the
# cheapest of the two known providers, and this project's actual default
# for every agent, so it under- rather than over-estimates on average.
_DEFAULT_PRICING_USD_PER_MILLION_TOKENS = (0.15, 0.60)


def estimate_cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    input_price, output_price = _PRICING_USD_PER_MILLION_TOKENS.get(
        (provider, model), _DEFAULT_PRICING_USD_PER_MILLION_TOKENS
    )
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


async def record_llm_usage(
    db: AsyncSession,
    *,
    agent: str,
    provider: str,
    model: str,
    usage: TokenUsage | None,
    repository_id: uuid.UUID | None,
    issue_id: uuid.UUID | None,
) -> None:
    """Persists one LLM call's token usage, committed immediately --
    independently of whatever transaction the calling agent loop is in,
    so usage data survives even if the agent's own work later fails and
    rolls back. Arguably more important to have cost visibility into a
    failed run than a successful one, and every agent calls this
    unconditionally right after every provider.complete(), success or
    not. usage is None only for a response that never reached the
    provider at all (see LLMResponse.usage's docstring) -- silently
    skipped rather than recording a misleading all-zero row.
    """
    if usage is None:
        return
    db.add(
        UsageRecord(
            repository_id=repository_id,
            issue_id=issue_id,
            agent=agent,
            provider=provider,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
    )
    await db.commit()


@dataclass
class AgentUsage:
    agent: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


@dataclass
class UsageSummary:
    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost_usd: float
    by_agent: list[AgentUsage]


async def _summarize_usage(
    db: AsyncSession, *, issue_id: uuid.UUID | None = None, repository_id: uuid.UUID | None = None
) -> UsageSummary:
    conditions = []
    if issue_id is not None:
        conditions.append(UsageRecord.issue_id == issue_id)
    if repository_id is not None:
        conditions.append(UsageRecord.repository_id == repository_id)

    result = await db.execute(
        select(
            UsageRecord.agent,
            UsageRecord.provider,
            UsageRecord.model,
            func.sum(UsageRecord.input_tokens),
            func.sum(UsageRecord.output_tokens),
        )
        .where(*conditions)
        .group_by(UsageRecord.agent, UsageRecord.provider, UsageRecord.model)
    )

    # Grouped by (agent, provider, model) for accurate per-model pricing,
    # then re-aggregated by agent alone for display -- an agent's calls
    # could in principle span more than one model if its provider setting
    # changed over time, and this still prices each model's share
    # correctly before summing.
    by_agent: dict[str, AgentUsage] = {}
    for agent, provider, model, input_tokens, output_tokens in result.all():
        cost = estimate_cost_usd(provider, model, input_tokens, output_tokens)
        existing = by_agent.get(agent)
        if existing is None:
            by_agent[agent] = AgentUsage(
                agent=agent,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
            )
        else:
            existing.input_tokens += input_tokens
            existing.output_tokens += output_tokens
            existing.estimated_cost_usd += cost

    agents = sorted(by_agent.values(), key=lambda a: a.agent)
    return UsageSummary(
        total_input_tokens=sum(a.input_tokens for a in agents),
        total_output_tokens=sum(a.output_tokens for a in agents),
        total_estimated_cost_usd=sum(a.estimated_cost_usd for a in agents),
        by_agent=agents,
    )


async def get_issue_usage_summary(db: AsyncSession, issue_id: uuid.UUID) -> UsageSummary:
    return await _summarize_usage(db, issue_id=issue_id)


async def get_repository_usage_summary(db: AsyncSession, repository_id: uuid.UUID) -> UsageSummary:
    return await _summarize_usage(db, repository_id=repository_id)
