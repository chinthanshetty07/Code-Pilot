import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.test_run import TestRun
from app.services.github_accounts import get_access_token
from app.services.sandbox import MAX_OUTPUT_CHARS, detect_test_setup, run_in_sandbox
from app.services.workspace import Workspace, create_workspace

logger = logging.getLogger(__name__)


async def run_tests(db: AsyncSession, test_run: TestRun) -> None:
    """Reconstructs the Coder agent's edited state in a fresh workspace
    (the original is long gone -- see Workspace.apply_diff) and runs the
    repository's tests inside a sandbox container. Never raises -- failures
    are recorded on the test_run itself (status="error"), mirroring
    create_plan()/create_code_change() so the job always completes and the
    failure is visible to the user."""
    test_run.status = "running"
    test_run.command = None
    test_run.output = None
    test_run.exit_code = None
    await db.commit()

    code_change = test_run.code_change
    issue = code_change.issue
    repository = issue.repository
    workspace: Workspace | None = None

    try:
        access_token = await get_access_token(db, repository.owner_id)
        workspace = await create_workspace(access_token, repository.full_name)

        if code_change.diff and code_change.diff.strip():
            await workspace.apply_diff(code_change.diff)

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
        else:
            test_run.exit_code = result.exit_code
            test_run.output = result.output[:MAX_OUTPUT_CHARS]
            test_run.status = "passed" if result.passed else "failed"
        await db.commit()

    except Exception as exc:
        logger.exception("Test run failed for code_change %s", code_change.id)
        await db.rollback()
        test_run.status = "error"
        test_run.output = str(exc)[:500]
        await db.commit()
    finally:
        if workspace is not None:
            workspace.cleanup()
