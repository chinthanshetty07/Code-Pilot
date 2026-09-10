import logging
from typing import Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import (
    READ_FILE_TOOL,
    SEARCH_CODE_TOOL,
    SEARCH_RESULT_LIMIT,
    format_search_results,
)
from app.core.config import get_settings
from app.llm.provider import Message, ToolCall, ToolSpec, get_llm_provider
from app.models.review import Review
from app.services.github_accounts import get_access_token
from app.services.search import search_code
from app.services.workspace import Workspace, WorkspaceError, create_workspace

logger = logging.getLogger(__name__)

# Read-only loop (no editing), closer in shape to the Planner's than the
# Coder's -- same order of magnitude as Planner's MAX_TURNS=8.
MAX_TURNS = 8
FORCE_SUBMIT_ATTEMPTS = 3

SUBMIT_REVIEW_TOOL = ToolSpec(
    name="submit_review",
    description=(
        "Submit your final review verdict once you've examined the diff (and read_file/"
        "search_code for any context you needed). Call this exactly once, when done."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["approved", "changes_requested"],
                "description": "Your overall verdict",
            },
            "summary": {
                "type": "string",
                "description": "2-4 sentence overall assessment explaining the verdict",
            },
            "comments": {
                "type": "array",
                "description": "Specific findings, if any -- can be empty for a clean approval",
                "items": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["blocking", "suggestion"],
                        },
                        "comment": {"type": "string"},
                    },
                    "required": ["file_path", "severity", "comment"],
                },
            },
        },
        "required": ["status", "summary", "comments"],
    },
)

REVIEWER_TOOLS = [SEARCH_CODE_TOOL, READ_FILE_TOOL, SUBMIT_REVIEW_TOOL]

SYSTEM_PROMPT = f"""You are the Code Reviewer agent in CodePilot, an AI software engineering tool.

You're given a developer's issue, the implementation plan that guided the change, and a diff
that has already passed the repository's test suite. Review it the way a careful, experienced
human reviewer would: correctness beyond whatever the tests happen to cover, whether it actually
implements the plan, code quality and consistency with the rest of the codebase's own style and
conventions, and anything else worth flagging before a human approves it. Passing tests is not
the same as being correct or well-written -- review the actual change, not just its test result.

Use read_file to see a changed file's real, current (post-change) content, and search_code for
broader context (how a changed function is used elsewhere, existing conventions) -- ground every
comment in code you've actually seen, never invent a file path or claim about code you haven't
looked at. Approving without looking at anything beyond the diff text is exactly the kind of
shortcut a careless human reviewer takes -- don't take it.

You have a hard budget of {MAX_TURNS} total tool calls for this whole task. Call submit_review
exactly once, when done: status is "approved" only if you have no blocking concerns (minor
suggestions don't block approval), "changes_requested" otherwise. comments can be empty for a
clean approval, but a non-empty verdict needs its reasoning in comments, not just the summary."""


class _ReviewComment(BaseModel):
    file_path: str
    severity: Literal["blocking", "suggestion"]
    comment: str


class _ReviewResult(BaseModel):
    status: Literal["approved", "changes_requested"]
    summary: str
    comments: list[_ReviewComment]


def _build_task_description(issue, plan, code_change) -> str:
    plan_section = f"Plan summary:\n{plan.summary}\n\n" if plan is not None else ""
    test_run = code_change.test_run
    test_section = (
        f"Tests passed with: {test_run.command}\n\n"
        if test_run is not None and test_run.status == "passed"
        else ""
    )
    return (
        f"Issue:\n{issue.description}\n\n"
        f"{plan_section}"
        f"The Coder agent's summary of this change:\n{code_change.summary or '(none)'}\n\n"
        f"{test_section}"
        f"Diff to review:\n{code_change.diff or '(empty diff)'}"
    )


async def _execute_tool(
    workspace: Workspace, db: AsyncSession, repository_id, call: ToolCall
) -> str:
    """Executes one non-submit_review tool call, returning its result as a
    string for the tool-result message. WorkspaceError is caught here (not
    left to the caller) and turned into an error string the model can act
    on -- same recoverable-failure pattern as app/agents/coder.py's
    _execute_tool, which this deliberately doesn't share code with despite
    the overlap (see app/agents/planner.py: each agent owns its own loop)."""
    try:
        if call.name == "search_code":
            results = await search_code(
                db, repository_id, call.arguments.get("query", ""), limit=SEARCH_RESULT_LIMIT
            )
            return format_search_results(results)
        if call.name == "read_file":
            return workspace.read_file(call.arguments.get("path", ""))
        return f"Unknown tool: {call.name}"
    except WorkspaceError as exc:
        return f"Error: {exc}"


async def create_review(db: AsyncSession, review: Review) -> None:
    """Run the Reviewer agent for one CodeChange: search_code/read_file/
    submit_review tool loop against a real git-backed checkout with the
    change's own diff already applied (so read_file shows the post-change
    state), capped at MAX_TURNS with the same forced-finish fallback proven
    for the Planner and Coder agents. Never raises -- failures are recorded
    on the review itself (status="failed"), mirroring create_code_change()
    so the job always completes and the failure is visible to the user."""
    review.status = "reviewing"
    review.error = None
    await db.commit()

    code_change = review.code_change
    issue = code_change.issue
    plan = issue.plan
    repository = issue.repository
    workspace: Workspace | None = None

    try:
        access_token = await get_access_token(db, repository.owner_id)
        workspace = await create_workspace(access_token, repository.full_name)
        if code_change.diff and code_change.diff.strip():
            await workspace.apply_diff(code_change.diff)

        provider = get_llm_provider(get_settings().reviewer_llm_provider)
        messages: list[Message] = [
            Message(role="user", content=_build_task_description(issue, plan, code_change))
        ]
        result: _ReviewResult | None = None

        for _turn in range(MAX_TURNS):
            response = await provider.complete(messages, tools=REVIEWER_TOOLS, system=SYSTEM_PROMPT)

            if not response.tool_calls:
                messages.append(Message(role="assistant", content=response.content))
                messages.append(
                    Message(
                        role="user",
                        content="Please continue your review, then call submit_review.",
                    )
                )
                continue

            messages.append(
                Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
            )

            for call in response.tool_calls:
                if call.name == "submit_review":
                    result = _ReviewResult.model_validate(call.arguments)
                    messages.append(
                        Message(role="tool", content="ok", tool_call_id=call.id, name=call.name)
                    )
                else:
                    tool_result = await _execute_tool(workspace, db, repository.id, call)
                    messages.append(
                        Message(
                            role="tool",
                            content=tool_result,
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )

            if result is not None:
                break

        if result is None:
            # See app/agents/planner.py's identical fallback for why this
            # exists and why it's shaped this way (schema narrowed to just
            # submit_review, not merely discouraged from the others; retried
            # a few times since even a forced attempt doesn't always land).
            for _attempt in range(FORCE_SUBMIT_ATTEMPTS):
                messages.append(
                    Message(
                        role="user",
                        content="You must call submit_review now, in this turn, with "
                        "whatever you've found so far. No other tool is available anymore.",
                    )
                )
                response = await provider.complete(
                    messages,
                    tools=[SUBMIT_REVIEW_TOOL],
                    system=SYSTEM_PROMPT,
                    force_tool="submit_review",
                )
                if response.tool_calls and response.tool_calls[0].name == "submit_review":
                    call = response.tool_calls[0]
                    messages.append(
                        Message(role="assistant", content=response.content, tool_calls=[call])
                    )
                    try:
                        result = _ReviewResult.model_validate(call.arguments)
                        break
                    except ValidationError:
                        messages.append(
                            Message(
                                role="tool",
                                content="That didn't match the required shape. Try again.",
                                tool_call_id=call.id,
                                name=call.name,
                            )
                        )
                else:
                    messages.append(Message(role="assistant", content=response.content))

        if result is None:
            raise ValueError(
                f"Reviewer did not submit a verdict within {MAX_TURNS} turns or "
                f"{FORCE_SUBMIT_ATTEMPTS} forced attempts"
            )

        review.status = result.status
        review.summary = result.summary
        review.comments = [c.model_dump() for c in result.comments]
        await db.commit()

    except Exception as exc:
        logger.exception("Review failed for code_change %s", code_change.id)
        await db.rollback()
        review.status = "failed"
        review.error = str(exc)[:500]
        await db.commit()
    finally:
        if workspace is not None:
            workspace.cleanup()
