import logging

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import SEARCH_CODE_TOOL, SEARCH_RESULT_LIMIT, format_search_results
from app.core.config import get_settings
from app.llm.provider import Message, ToolSpec, get_llm_provider
from app.models.issue import Issue
from app.models.plan import Plan
from app.services.search import search_code
from app.services.usage import record_llm_usage

logger = logging.getLogger(__name__)

MAX_TURNS = 8
# Once a model has made this many search_code calls, a reminder is appended
# to its next search result nudging it toward submit_plan. Purely a nudge --
# the free-choice phase can still run out without ever submitting.
SEARCH_NUDGE_THRESHOLD = 3
# If the free-choice phase above exhausts MAX_TURNS without a submission,
# this many additional attempts explicitly force submit_plan (see the
# comment where it's used) before giving up entirely.
FORCE_SUBMIT_ATTEMPTS = 3
# Confirmed live (against a real, previously-failing production issue) that
# capping each search_code result's own size (see
# MAX_SEARCH_RESULT_CONTENT_CHARS in app/agents/tools.py) is NOT enough on
# its own -- the model can just make more search_code calls to compensate,
# so total accumulated conversation size keeps climbing regardless of any
# single call's cap, until it exceeds Groq's 8000 TPM limit on the
# free/on_demand tier.
#
# The first version of this safeguard used the provider's own reported
# input_tokens from the *previous* turn to decide whether to allow the
# *next* one -- reasonable-sounding, but confirmed live to still fail: that
# number describes what was sent to produce the previous response, not what
# has accumulated *since* (that turn's own tool call and tool result get
# appended afterward, growing `messages` further before the next check ever
# runs) -- a one-turn lag that reliably let exactly one more, large
# search_code call through right when it mattered most.
#
# This instead estimates the *current*, already-appended size of `messages`
# directly, every time through the loop, so there's no lag: the check
# reflects exactly what the next request would actually send. Character-based
# rather than an exact token count (no tokenizer dependency needed), using a
# conservative ratio calibrated against real recorded usage while iterating
# on this fix: code/JSON content tokenizes far more densely than English
# prose (observed ~2.2-2.6 chars/token for real search_code results here,
# not the ~4 chars/token rule of thumb that prose would suggest).
_CHARS_PER_TOKEN_ESTIMATE = 2.2
MAX_ESTIMATED_TOKENS_BEFORE_FORCING_SUBMIT = 4500
# A second, higher checkpoint inside the force-submit phase itself (see
# where it's used) -- that phase doesn't offer search_code, so it grows
# much more slowly than the free-choice phase above, and needs a bigger
# ceiling to not trip on completely ordinary conversations that only
# entered force-submit because MAX_TURNS ran out (not because of size).
MAX_ESTIMATED_TOKENS_HARD_CEILING = 7000


def _estimate_conversation_tokens(messages: list[Message]) -> float:
    chars = 0
    for m in messages:
        if m.content:
            chars += len(m.content)
        for call in m.tool_calls:
            chars += len(call.name) + len(str(call.arguments))
    return chars / _CHARS_PER_TOKEN_ESTIMATE

SUBMIT_PLAN_TOOL = ToolSpec(
    name="submit_plan",
    description=(
        "Submit the final implementation plan once you've gathered enough context via "
        "search_code. Call this exactly once, when done -- not before you've actually "
        "searched the repository."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "1-2 sentence description of the fix or change",
            },
            "relevant_files": {
                "type": "array",
                "description": "Files relevant to this issue, and why each one matters",
                "items": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["file_path", "reason"],
                },
            },
            "implementation_steps": {
                "type": "array",
                "description": "Ordered, concrete steps to implement the fix",
                "items": {"type": "string"},
            },
            "tests_to_add_or_change": {
                "type": "array",
                "description": "What test coverage should be added or updated",
                "items": {"type": "string"},
            },
            "risks": {
                "type": "array",
                "description": "Anything that could go wrong or needs careful attention",
                "items": {"type": "string"},
            },
        },
        "required": [
            "summary",
            "relevant_files",
            "implementation_steps",
            "tests_to_add_or_change",
            "risks",
        ],
    },
)

SYSTEM_PROMPT = f"""You are the Planner agent in CodePilot, an AI software engineering tool.

Given a developer's issue description, investigate the repository and produce a structured
implementation plan. You do not write code yourself -- that's a separate agent's job.

Use the search_code tool to find relevant files, functions, and classes. Ground every claim
in what search_code actually returned -- never invent a file path or symbol you haven't
actually seen in a search result.

You have a hard budget of {MAX_TURNS} total turns for this whole task, search_code calls
included -- this is not unlimited. In practice, 2-4 searches with well-chosen, varied queries
(the reported symptom, the likely component, related tests) is enough to understand most
issues. Do not keep searching once you have a reasonable picture: prefer calling submit_plan
a little early over exhausting your turns still searching. Call submit_plan exactly once,
when done."""


class _RelevantFile(BaseModel):
    file_path: str
    reason: str


class _PlanData(BaseModel):
    """Validates the LLM's submit_plan arguments before they're trusted
    enough to write to the database -- gives a clear error message instead
    of a raw KeyError/TypeError if a model returns a malformed shape."""

    summary: str
    relevant_files: list[_RelevantFile]
    implementation_steps: list[str]
    tests_to_add_or_change: list[str]
    risks: list[str]


async def create_plan(db: AsyncSession, issue: Issue) -> None:
    """Run the Planner agent for one issue: search_code + submit_plan tool
    loop, capped at MAX_TURNS. Never raises -- failures are recorded on the
    issue itself (planning_status="failed"), mirroring index_repository()
    so the job always completes and the failure is visible to the user."""
    issue.planning_status = "planning"
    issue.planning_error = None
    await db.commit()

    try:
        provider_name = get_settings().planner_llm_provider
        provider = get_llm_provider(provider_name)
        messages: list[Message] = [Message(role="user", content=issue.description)]
        plan_data: _PlanData | None = None
        search_call_count = 0

        for _turn in range(MAX_TURNS):
            estimated_tokens = _estimate_conversation_tokens(messages)
            if estimated_tokens >= MAX_ESTIMATED_TOKENS_BEFORE_FORCING_SUBMIT:
                # See MAX_ESTIMATED_TOKENS_BEFORE_FORCING_SUBMIT's own
                # comment. Checked against `messages`' actual current
                # content -- already reflects every turn completed so far,
                # so there's no lag between "conversation is already large"
                # and this check seeing it. Falls through to the
                # force-submit phase below, which already excludes
                # search_code.
                break

            response = await provider.complete(
                messages, tools=[SEARCH_CODE_TOOL, SUBMIT_PLAN_TOOL], system=SYSTEM_PROMPT
            )
            await record_llm_usage(
                db,
                agent="planner",
                provider=provider_name,
                model=provider.model,
                usage=response.usage,
                repository_id=issue.repository_id,
                issue_id=issue.id,
            )

            if not response.tool_calls:
                # Answered in plain text instead of calling a tool -- nudge
                # it back on track rather than treating this as done.
                messages.append(Message(role="assistant", content=response.content))
                messages.append(
                    Message(
                        role="user",
                        content="Please continue using search_code, then call submit_plan "
                        "with your findings.",
                    )
                )
                continue

            messages.append(
                Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
            )

            for call in response.tool_calls:
                if call.name == "submit_plan":
                    plan_data = _PlanData.model_validate(call.arguments)
                    messages.append(
                        Message(role="tool", content="ok", tool_call_id=call.id, name=call.name)
                    )
                elif call.name == "search_code":
                    search_call_count += 1
                    results = await search_code(
                        db,
                        issue.repository_id,
                        call.arguments.get("query", ""),
                        limit=SEARCH_RESULT_LIMIT,
                    )
                    content = format_search_results(results)
                    if search_call_count >= SEARCH_NUDGE_THRESHOLD:
                        content += (
                            f"\n\n[You have now called search_code {search_call_count} "
                            "times. If you have a reasonable picture of the relevant code, "
                            "call submit_plan now rather than searching further.]"
                        )
                    messages.append(
                        Message(
                            role="tool",
                            content=content,
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )
                else:
                    messages.append(
                        Message(
                            role="tool",
                            content=f"Unknown tool: {call.name}",
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )

            if plan_data is not None:
                break

        if plan_data is None:
            # The free-choice phase ran out without the model ever calling
            # submit_plan -- observed in practice with Groq's gpt-oss-120b,
            # which can get fixated on repeatedly searching well past the
            # point of having enough context. Force it explicitly instead of
            # failing outright: a plan based on partial context is still far
            # more useful to the user than no plan at all.
            #
            # search_code is dropped from `tools` entirely here, not just
            # discouraged via tool_choice -- also observed in practice: a
            # sufficiently fixated model can still *attempt* a tool that
            # isn't even declared anymore, which the provider then has to
            # reject outright since there's nothing valid to fall back to.
            # If the model can't see search_code as an option, it can't try
            # it. Retried a few times since even a forced, narrowed turn
            # doesn't always land on the first attempt.
            for _attempt in range(FORCE_SUBMIT_ATTEMPTS):
                if _estimate_conversation_tokens(messages) >= MAX_ESTIMATED_TOKENS_HARD_CEILING:
                    # Belt-and-braces: force-submit doesn't offer search_code
                    # so it grows much more slowly than the free-choice phase
                    # did, but a malformed-submission retry (each appending
                    # "didn't match shape, try again" + a new response) could
                    # still theoretically add up across FORCE_SUBMIT_ATTEMPTS
                    # tries. Bailing out here still gets a clear, honest
                    # "failed" result via the ValueError below instead of
                    # risking the exact 413 this whole mechanism exists to
                    # prevent.
                    break

                messages.append(
                    Message(
                        role="user",
                        content="You must call submit_plan now, in this turn, with "
                        "whatever information you've already gathered. search_code is "
                        "no longer available. A plan based on partial context is "
                        "required, and is better than no plan at all.",
                    )
                )
                response = await provider.complete(
                    messages,
                    tools=[SUBMIT_PLAN_TOOL],
                    system=SYSTEM_PROMPT,
                    force_tool="submit_plan",
                )
                await record_llm_usage(
                    db,
                    agent="planner",
                    provider=provider_name,
                    model=provider.model,
                    usage=response.usage,
                    repository_id=issue.repository_id,
                    issue_id=issue.id,
                )

                if response.tool_calls and response.tool_calls[0].name == "submit_plan":
                    call = response.tool_calls[0]
                    messages.append(
                        Message(role="assistant", content=response.content, tool_calls=[call])
                    )
                    try:
                        plan_data = _PlanData.model_validate(call.arguments)
                        break
                    except ValidationError:
                        messages.append(
                            Message(
                                role="tool",
                                content="That submission didn't match the required "
                                "shape. Try again.",
                                tool_call_id=call.id,
                                name=call.name,
                            )
                        )
                else:
                    messages.append(Message(role="assistant", content=response.content))

        if plan_data is None:
            raise ValueError(
                f"Planner did not submit a plan within {MAX_TURNS} turns or "
                f"{FORCE_SUBMIT_ATTEMPTS} forced attempts"
            )

        db.add(
            Plan(
                issue_id=issue.id,
                summary=plan_data.summary,
                relevant_files=[f.model_dump() for f in plan_data.relevant_files],
                implementation_steps=plan_data.implementation_steps,
                tests_to_add_or_change=plan_data.tests_to_add_or_change,
                risks=plan_data.risks,
            )
        )
        issue.planning_status = "planned"
        await db.commit()

    except Exception as exc:
        logger.exception("Planning failed for issue %s", issue.id)
        await db.rollback()
        issue.planning_status = "failed"
        issue.planning_error = str(exc)[:500]
        await db.commit()
