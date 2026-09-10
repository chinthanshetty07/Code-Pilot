import logging

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import SEARCH_CODE_TOOL, SEARCH_RESULT_LIMIT, format_search_results
from app.core.config import get_settings
from app.llm.provider import LLMProvider, Message, ToolCall, ToolSpec, get_llm_provider
from app.models.code_change import CodeChange
from app.models.test_run import TestRun
from app.services.github_accounts import get_access_token
from app.services.search import search_code
from app.services.workspace import Workspace, WorkspaceError, create_workspace

logger = logging.getLogger(__name__)

# More headroom than the Planner's MAX_TURNS=8: reading and editing several
# files legitimately takes more tool calls than searching alone does.
MAX_TURNS = 15
# Same fallback as the Planner (see app/agents/planner.py) for the same
# reason -- a model fixated on one tool past the point of usefulness.
FORCE_FINISH_ATTEMPTS = 3

READ_FILE_TOOL = ToolSpec(
    name="read_file",
    description=(
        "Read a file's current, real content from the repository, by path relative to the "
        "repository root. Always read a file before editing it if you haven't already seen "
        "its exact current content in this conversation -- edit_file requires an exact match."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the repository root"}
        },
        "required": ["path"],
    },
)

CREATE_FILE_TOOL = ToolSpec(
    name="create_file",
    description=(
        "Create a brand new file with the given content. Fails if the file already exists -- "
        "use edit_file to change an existing one."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the repository root",
            },
            "content": {"type": "string", "description": "The full content of the new file"},
        },
        "required": ["path", "content"],
    },
)

EDIT_FILE_TOOL = ToolSpec(
    name="edit_file",
    description=(
        "Replace an exact, unique snippet of an existing file's content with new content. "
        "old_string must match the file's current content exactly, including whitespace, and "
        "appear exactly once in the file -- include enough surrounding context to make it "
        "unique. Read the file first if you aren't sure of its exact current text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the repository root",
            },
            "old_string": {"type": "string", "description": "The exact existing text to replace"},
            "new_string": {"type": "string", "description": "The text to replace it with"},
        },
        "required": ["path", "old_string", "new_string"],
    },
)

GET_GIT_DIFF_TOOL = ToolSpec(
    name="get_git_diff",
    description=(
        "See the git diff of everything you've changed so far in this workspace, to check "
        "your own progress."
    ),
    parameters={"type": "object", "properties": {}},
)

FINISH_TOOL = ToolSpec(
    name="finish",
    description=(
        "Call this exactly once, when you've finished making all the changes needed. The "
        "actual diff is captured automatically -- you don't report it yourself, just summarize."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "What you changed and why, 2-4 sentences",
            }
        },
        "required": ["summary"],
    },
)

CODER_TOOLS = [
    SEARCH_CODE_TOOL,
    READ_FILE_TOOL,
    CREATE_FILE_TOOL,
    EDIT_FILE_TOOL,
    GET_GIT_DIFF_TOOL,
    FINISH_TOOL,
]

SYSTEM_PROMPT = f"""You are the Coder agent in CodePilot, an AI software engineering tool.

You are given a developer's issue and an implementation plan already produced by the Planner
agent. Make the actual code changes in the repository using the tools available: search_code
for more context if the plan alone isn't enough, read_file before editing anything you haven't
already seen the exact current content of, create_file for brand new files, edit_file to change
existing ones, and get_git_diff to check your own progress.

Ground every change in real file content you've actually read -- never guess at what a file
currently contains or invent code that isn't there. Match the existing code's own style and
conventions. You have a hard budget of {MAX_TURNS} total tool calls for this whole task.

Call finish exactly once, when your changes are complete."""

# Tail of a failing test's captured output included in a fix attempt's
# prompt -- pytest/jest failures put the actual assertion/traceback at the
# END of the output, with potentially thousands of characters of setup or
# passing-test noise before it, so this takes the last N characters rather
# than the first. Keeps the prompt focused and cheap without real log
# parsing.
FIX_OUTPUT_TAIL_CHARS = 4000

FIX_SYSTEM_PROMPT = f"""You are the Coder agent in CodePilot, an AI software engineering tool.

You previously made a code change for a developer's issue, but the repository's test suite
failed against it. You're given the original issue, your own summary of what you changed
before, and the exact command and output of the failing test run. The workspace already
reflects your previous change -- you're continuing from there, not starting over. Diagnose the
real cause of the failure from the output before changing anything, then fix it using the tools
available: search_code for more context, read_file before editing anything you haven't already
seen the exact current content of, create_file for brand new files, edit_file to change existing
ones, and get_git_diff to check your progress against the *original* repository state (it
reflects your earlier change plus whatever you do now, combined).

Ground every change in real file content you've actually read -- never guess at what a file
currently contains or invent code that isn't there. Match the existing code's own style and
conventions. You have a hard budget of {MAX_TURNS} total tool calls for this whole task.

Call finish exactly once, when you're done, summarizing the change's current full state (not
just what you changed in this fix pass -- the summary you give replaces the previous one)."""


class _CodeChangeResult(BaseModel):
    summary: str


def _build_task_description(issue, plan) -> str:
    files = (
        "\n".join(f"- {f['file_path']}: {f['reason']}" for f in plan.relevant_files)
        or "(none listed)"
    )
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(plan.implementation_steps, start=1))
    tests = "\n".join(f"- {t}" for t in plan.tests_to_add_or_change) or "(none listed)"
    return (
        f"Issue:\n{issue.description}\n\n"
        f"Plan summary:\n{plan.summary}\n\n"
        f"Relevant files (from the Planner's search -- verify with read_file, don't trust "
        f"blindly):\n{files}\n\n"
        f"Implementation steps:\n{steps}\n\n"
        f"Tests to add or change:\n{tests}"
    )


def _build_fix_description(issue, plan, code_change: CodeChange, test_run: TestRun) -> str:
    plan_section = f"Plan summary:\n{plan.summary}\n\n" if plan is not None else ""
    output = test_run.output or "(no output captured)"
    if len(output) > FIX_OUTPUT_TAIL_CHARS:
        output = f"[... earlier output truncated ...]\n{output[-FIX_OUTPUT_TAIL_CHARS:]}"
    return (
        f"Issue:\n{issue.description}\n\n"
        f"{plan_section}"
        f"Your previous summary of this change:\n{code_change.summary or '(none)'}\n\n"
        f"You ran the repository's tests and they failed. Diagnose the failure from the "
        f"output below, then fix it.\n\n"
        f"Test command:\n{test_run.command}\n\n"
        f"Captured output:\n{output}"
    )


async def _execute_tool(
    workspace: Workspace, db: AsyncSession, repository_id, call: ToolCall
) -> str:
    """Executes one non-finish tool call, returning its result as a string
    for the tool-result message. WorkspaceError is caught here (not left to
    the caller) and turned into an error string the model can act on --
    the same recoverable-failure pattern as a bad search_code query."""
    try:
        if call.name == "search_code":
            results = await search_code(
                db, repository_id, call.arguments.get("query", ""), limit=SEARCH_RESULT_LIMIT
            )
            return format_search_results(results)
        if call.name == "read_file":
            return workspace.read_file(call.arguments.get("path", ""))
        if call.name == "create_file":
            workspace.create_file(call.arguments.get("path", ""), call.arguments.get("content", ""))
            return f"Created {call.arguments.get('path')}"
        if call.name == "edit_file":
            workspace.edit_file(
                call.arguments.get("path", ""),
                call.arguments.get("old_string", ""),
                call.arguments.get("new_string", ""),
            )
            return f"Edited {call.arguments.get('path')}"
        if call.name == "get_git_diff":
            diff = await workspace.git_diff()
            return diff if diff.strip() else "(no changes yet)"
        return f"Unknown tool: {call.name}"
    except WorkspaceError as exc:
        return f"Error: {exc}"


async def _run_coding_loop(
    provider: LLMProvider,
    workspace: Workspace,
    db: AsyncSession,
    repository_id,
    system: str,
    initial_message: str,
) -> _CodeChangeResult:
    """Shared tool loop + forced-finish fallback: search_code/read_file/
    create_file/edit_file/finish against a real git-backed checkout,
    capped at MAX_TURNS with the same forced-finish fallback proven for
    the Planner agent (see app/agents/planner.py for why this needs all
    three of a narrowed schema, forced tool_choice, and explicit
    plain-language reinforcement together). Used for both a fresh code
    generation and a fix attempt -- they differ only in the system prompt
    and the message that starts the conversation, not in how convergence
    is reached."""
    messages: list[Message] = [Message(role="user", content=initial_message)]
    result: _CodeChangeResult | None = None

    for _turn in range(MAX_TURNS):
        response = await provider.complete(messages, tools=CODER_TOOLS, system=system)

        if not response.tool_calls:
            messages.append(Message(role="assistant", content=response.content))
            messages.append(
                Message(
                    role="user",
                    content="Please continue making the necessary changes, then call finish.",
                )
            )
            continue

        messages.append(
            Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
        )

        for call in response.tool_calls:
            if call.name == "finish":
                result = _CodeChangeResult.model_validate(call.arguments)
                messages.append(
                    Message(role="tool", content="ok", tool_call_id=call.id, name=call.name)
                )
            else:
                tool_result = await _execute_tool(workspace, db, repository_id, call)
                messages.append(
                    Message(role="tool", content=tool_result, tool_call_id=call.id, name=call.name)
                )

        if result is not None:
            break

    if result is None:
        # See app/agents/planner.py's identical fallback for why this
        # exists and why it's shaped this way (schema narrowed to just
        # finish, not merely discouraged from the others; retried a few
        # times since even a forced attempt doesn't always land).
        for _attempt in range(FORCE_FINISH_ATTEMPTS):
            messages.append(
                Message(
                    role="user",
                    content="You must call finish now, in this turn, summarizing "
                    "whatever changes you've already made. No other tool is available "
                    "anymore.",
                )
            )
            response = await provider.complete(
                messages, tools=[FINISH_TOOL], system=system, force_tool="finish"
            )
            if response.tool_calls and response.tool_calls[0].name == "finish":
                call = response.tool_calls[0]
                messages.append(
                    Message(role="assistant", content=response.content, tool_calls=[call])
                )
                try:
                    result = _CodeChangeResult.model_validate(call.arguments)
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
            f"Coder did not finish within {MAX_TURNS} turns or "
            f"{FORCE_FINISH_ATTEMPTS} forced attempts"
        )

    return result


async def create_code_change(db: AsyncSession, code_change: CodeChange) -> None:
    """Run the Coder agent for one CodeChange against a fresh, real
    git-backed checkout of the repository. Never raises -- failures are
    recorded on the code_change itself, so the job always completes and
    the failure is visible to the user."""
    code_change.generation_status = "generating"
    code_change.generation_error = None
    await db.commit()

    issue = code_change.issue
    plan = issue.plan
    repository = issue.repository
    workspace: Workspace | None = None

    try:
        if plan is None:
            raise ValueError("This issue has no plan yet -- run the Planner first")

        access_token = await get_access_token(db, repository.owner_id)
        workspace = await create_workspace(access_token, repository.full_name)

        provider = get_llm_provider(get_settings().coder_llm_provider)
        result = await _run_coding_loop(
            provider,
            workspace,
            db,
            repository.id,
            SYSTEM_PROMPT,
            _build_task_description(issue, plan),
        )

        diff = await workspace.git_diff()
        code_change.summary = result.summary
        code_change.diff = diff
        if diff.strip():
            code_change.generation_status = "generated"
        else:
            code_change.generation_status = "failed"
            code_change.generation_error = "The agent finished without making any file changes."
        await db.commit()

    except Exception as exc:
        logger.exception("Code generation failed for code_change %s", code_change.id)
        await db.rollback()
        code_change.generation_status = "failed"
        code_change.generation_error = str(exc)[:500]
        await db.commit()
    finally:
        if workspace is not None:
            workspace.cleanup()


async def fix_code_change(
    db: AsyncSession,
    workspace: Workspace,
    repository_id,
    issue,
    plan,
    code_change: CodeChange,
    test_run: TestRun,
) -> str:
    """Runs one fix attempt against an already-prepared workspace (the
    previous diff already applied, a failing test already run there) --
    shares _run_coding_loop with create_code_change, just seeded with the
    failure instead of a fresh issue+plan. Returns the new summary; the
    caller (app/services/test_runner.py) recomputes the diff via
    workspace.git_diff() once this returns and is responsible for
    persisting both -- this function doesn't commit anything itself, it's
    pure agent-loop logic, the same division of responsibility
    create_code_change has with its own caller."""
    provider = get_llm_provider(get_settings().coder_llm_provider)
    result = await _run_coding_loop(
        provider,
        workspace,
        db,
        repository_id,
        FIX_SYSTEM_PROMPT,
        _build_fix_description(issue, plan, code_change, test_run),
    )
    return result.summary
