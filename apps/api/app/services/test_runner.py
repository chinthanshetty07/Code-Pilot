import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.coder import fix_code_change
from app.models.test_run import TestRun
from app.services.github_accounts import get_access_token
from app.services.sandbox import MAX_OUTPUT_CHARS, detect_test_setup, run_in_sandbox
from app.services.workspace import Workspace, create_workspace

logger = logging.getLogger(__name__)

# How many times the fix loop will ask the Coder agent to fix a failing
# test and re-run it before giving up and leaving the result as "failed".
# No real relationship to coder.py's FORCE_FINISH_ATTEMPTS beyond both
# landing on "a handful of retries is enough to be useful without burning
# unbounded LLM/sandbox time on a change that isn't converging."
MAX_FIX_ATTEMPTS = 3


async def run_tests(db: AsyncSession, test_run: TestRun) -> None:
    """Reconstructs the Coder agent's edited state in a fresh workspace
    (the original is long gone -- see Workspace.apply_diff) and runs the
    repository's tests inside a sandbox container. If they fail, this is
    also the "Failure Analysis / Fix Loop" stage of the pipeline: it asks
    the Coder agent to fix the code in the same workspace and tries again,
    up to MAX_FIX_ATTEMPTS times, before settling on a final result --
    folded into this one job rather than a separate one, since from the
    caller's perspective it's still just "the answer to did the tests
    pass" for this code_change.

    Never raises -- failures are recorded on the test_run itself
    (status="error"), mirroring create_plan()/create_code_change() so the
    job always completes and the failure is visible to the user. A fix
    *attempt* itself blowing up (as opposed to the tests it's trying to
    fix) is handled separately, without touching that outer status="error"
    path: it leaves the last real test verdict ("failed") in place rather
    than masking it behind "error", since the tests genuinely did run and
    did fail -- that's real information, independent of whether the fix
    itself succeeded.
    """
    test_run.status = "running"
    test_run.command = None
    test_run.output = None
    test_run.exit_code = None
    test_run.fix_attempts = 0
    await db.commit()

    code_change = test_run.code_change
    issue = code_change.issue
    plan = issue.plan
    repository = issue.repository
    workspace: Workspace | None = None

    try:
        access_token = await get_access_token(db, repository.owner_id)
        workspace = await create_workspace(access_token, repository.full_name)

        if code_change.diff and code_change.diff.strip():
            await workspace.apply_diff(code_change.diff)

        while True:
            setup = detect_test_setup(workspace.root)
            if setup is None:
                test_run.status = "error"
                test_run.output = (
                    "Couldn't detect a test command for this repository -- no "
                    "package.json with a test script, requirements.txt, "
                    "pyproject.toml, setup.py, or test_*.py files were found."
                )
                await db.commit()
                return

            test_run.command = setup.shell_command
            await db.commit()

            result = await run_in_sandbox(workspace.root, setup)

            if result.timed_out:
                test_run.status = "error"
                test_run.output = "The test run timed out."
                await db.commit()
                return

            test_run.exit_code = result.exit_code
            test_run.output = result.output[:MAX_OUTPUT_CHARS]
            test_run.status = "passed" if result.passed else "failed"
            await db.commit()

            if result.passed or test_run.fix_attempts >= MAX_FIX_ATTEMPTS:
                return

            try:
                test_run.status = "fixing"
                test_run.fix_attempts += 1
                await db.commit()

                summary = await fix_code_change(
                    db, workspace, repository.id, issue, plan, code_change, test_run
                )
                code_change.summary = summary
                code_change.diff = await workspace.git_diff()
                await db.commit()
            except Exception:
                logger.exception(
                    "Fix attempt %d failed for test_run %s", test_run.fix_attempts, test_run.id
                )
                test_run.status = "failed"
                test_run.output = (test_run.output or "") + (
                    f"\n\n[Fix attempt {test_run.fix_attempts} raised an error and was "
                    "skipped -- the result above is from the last real test run.]"
                )
                await db.commit()
                return

    except Exception as exc:
        logger.exception("Test run failed for code_change %s", code_change.id)
        await db.rollback()
        test_run.status = "error"
        test_run.output = str(exc)[:500]
        await db.commit()
    finally:
        if workspace is not None:
            workspace.cleanup()
