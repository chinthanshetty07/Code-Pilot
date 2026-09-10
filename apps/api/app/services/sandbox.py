"""Runs a repository's test suite inside an ephemeral, resource-limited
Docker sibling container -- real isolation for code whose correctness (and,
since it's LLM-generated, whose *intent*) hasn't been reviewed by a human
yet, not just a subprocess call with a timeout.

Sibling containers, not nested ones: the worker container talks to the
*host's* Docker socket (mounted in via docker-compose.yml, worker service
only) to ask the same daemon that's running the worker itself to also run
a throwaway test container alongside it. This means a bind-mounted volume
source path is resolved by the daemon against its own host filesystem, not
this worker container's private one -- so getting the workspace's files
into the sandbox container uses `docker cp` (which streams bytes through
the Docker API from whatever process calls it) instead of `docker run -v`,
sidestepping that mismatch entirely rather than working around it with
host-path bookkeeping.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300
MAX_OUTPUT_CHARS = 50_000
SANDBOX_MEMORY_LIMIT = "1g"
SANDBOX_CPU_LIMIT = "1"


class SandboxError(Exception):
    """Raised for sandbox infrastructure failures (a docker command itself
    failing) -- distinct from a test *failing*, which is a normal, expected
    outcome recorded as a result, not an exception."""


@dataclass
class TestSetup:
    image: str
    test_command: str
    install_command: str | None = None

    @property
    def shell_command(self) -> str:
        if self.install_command:
            return f"{self.install_command} && {self.test_command}"
        return self.test_command


@dataclass
class SandboxResult:
    exit_code: int | None  # None only when timed_out is True
    output: str
    timed_out: bool

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.exit_code == 0


def detect_test_setup(workspace_root: Path) -> TestSetup | None:
    """Best-effort test command detection for common, conventional project
    layouts -- deliberately not trying to handle every build tool (poetry,
    pipenv, yarn/pnpm workspaces, ...). Returns None (rather than guessing)
    when nothing recognizable is found."""
    package_json_path = workspace_root / "package.json"
    if package_json_path.is_file():
        try:
            package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            package_json = {}
        if isinstance(package_json, dict) and "test" in package_json.get("scripts", {}):
            install = (
                "npm ci" if (workspace_root / "package-lock.json").is_file() else "npm install"
            )
            return TestSetup(image="node:20-slim", install_command=install, test_command="npm test")

    if (workspace_root / "requirements.txt").is_file():
        return TestSetup(
            image="python:3.12-slim",
            install_command="pip install --quiet -r requirements.txt",
            test_command="pytest",
        )
    if (workspace_root / "pyproject.toml").is_file() or (workspace_root / "setup.py").is_file():
        return TestSetup(
            image="python:3.12-slim",
            install_command="pip install --quiet -e .",
            test_command="pytest",
        )
    has_python_tests = next(workspace_root.rglob("test_*.py"), None) is not None or (
        next(workspace_root.rglob("*_test.py"), None) is not None
    )
    if has_python_tests:
        return TestSetup(
            image="python:3.12-slim",
            install_command="pip install --quiet pytest",
            test_command="pytest",
        )

    return None


async def _docker(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise SandboxError(f"docker {args[0]} failed: {stderr.decode(errors='replace')}")
    return stdout.decode(errors="replace")


async def run_in_sandbox(
    workspace_root: Path,
    setup: TestSetup,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> SandboxResult:
    container_name = f"codepilot-sandbox-{uuid.uuid4().hex[:12]}"

    # Created idle (not running the real command yet) so the workspace can
    # be copied in first -- see the module docstring for why cp instead of
    # a bind mount.
    await _docker(
        "create",
        "--name",
        container_name,
        "--memory",
        SANDBOX_MEMORY_LIMIT,
        "--cpus",
        SANDBOX_CPU_LIMIT,
        setup.image,
        "sleep",
        str(timeout_seconds + 60),
    )
    try:
        await _docker("start", container_name)
        await _docker("cp", f"{workspace_root}/.", f"{container_name}:/workspace")

        process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-w",
            "/workspace",
            container_name,
            "sh",
            "-c",
            setup.shell_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            # `docker rm -f` in the `finally` block below tears down the
            # container (and everything running inside it) regardless of
            # whether this local client-side kill takes effect -- that's
            # the real guarantee against a runaway process, this is just
            # tidiness for the local `docker exec` invocation itself.
            process.kill()
            return SandboxResult(exit_code=None, output="", timed_out=True)

        output = stdout.decode(errors="replace")
        return SandboxResult(
            exit_code=process.returncode,
            output=output[:MAX_OUTPUT_CHARS],
            timed_out=False,
        )
    finally:
        # --rm isn't used at create time since this container's exit is
        # driven by `docker rm -f` here, not the sleep command finishing.
        try:
            await _docker("rm", "-f", container_name)
        except SandboxError:
            logger.warning("Failed to remove sandbox container %s", container_name)
