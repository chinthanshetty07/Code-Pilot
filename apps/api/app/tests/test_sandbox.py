"""Real integration tests against the actual local Docker daemon (via
Colima) -- this is genuinely new infrastructure (sibling containers via a
mounted socket, docker cp instead of a bind mount), so it's tested against
real `docker` invocations rather than mocked. These are slower than the
rest of the suite (each spins up a real container) but that's the point:
the whole risk here is in the exact command sequence actually working."""

from pathlib import Path

import pytest

from app.services.sandbox import (
    SandboxError,
    TestSetup,
    detect_test_setup,
    run_in_sandbox,
)


def test_detect_test_setup_prefers_requirements_txt(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")

    setup = detect_test_setup(tmp_path)

    assert setup is not None
    assert setup.image == "python:3.12-slim"
    assert "requirements.txt" in (setup.install_command or "")
    assert setup.test_command == "pytest"


def test_detect_test_setup_falls_back_to_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")

    setup = detect_test_setup(tmp_path)

    assert setup is not None
    assert "-e ." in (setup.install_command or "")


def test_detect_test_setup_falls_back_to_bare_pytest_files(tmp_path: Path) -> None:
    (tmp_path / "test_something.py").write_text("def test_x(): assert True\n")

    setup = detect_test_setup(tmp_path)

    assert setup is not None
    assert setup.install_command == "pip install --quiet pytest"


def test_detect_test_setup_prefers_npm_test_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    (tmp_path / "package-lock.json").write_text("{}")

    setup = detect_test_setup(tmp_path)

    assert setup is not None
    assert setup.image == "node:20-slim"
    assert setup.install_command == "npm ci"
    assert setup.test_command == "npm test"


def test_detect_test_setup_ignores_package_json_without_test_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"build": "webpack"}}')

    assert detect_test_setup(tmp_path) is None


def test_detect_test_setup_returns_none_when_nothing_recognizable(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello\n")

    assert detect_test_setup(tmp_path) is None


def test_test_setup_shell_command_joins_install_and_test() -> None:
    setup = TestSetup(
        image="python:3.12-slim", install_command="pip install x", test_command="pytest"
    )
    assert setup.shell_command == "pip install x && pytest"


def test_test_setup_shell_command_without_install() -> None:
    setup = TestSetup(image="python:3.12-slim", test_command="pytest")
    assert setup.shell_command == "pytest"


async def test_run_in_sandbox_reports_a_passing_command(tmp_path: Path) -> None:
    (tmp_path / "check.py").write_text("print('hello from sandbox')\n")
    setup = TestSetup(image="python:3.12-slim", test_command="python check.py")

    result = await run_in_sandbox(tmp_path, setup, timeout_seconds=60)

    assert result.passed
    assert result.exit_code == 0
    assert not result.timed_out
    assert "hello from sandbox" in result.output


async def test_run_in_sandbox_reports_a_failing_command(tmp_path: Path) -> None:
    (tmp_path / "check.py").write_text("import sys; print('boom'); sys.exit(1)\n")
    setup = TestSetup(image="python:3.12-slim", test_command="python check.py")

    result = await run_in_sandbox(tmp_path, setup, timeout_seconds=60)

    assert not result.passed
    assert result.exit_code == 1
    assert "boom" in result.output


async def test_run_in_sandbox_copies_the_real_workspace_contents(tmp_path: Path) -> None:
    """Confirms docker cp is actually landing the right files at /workspace
    -- the whole point of this milestone's docker-cp-instead-of-bind-mount
    design (see app/services/sandbox.py's module docstring)."""
    (tmp_path / "data.txt").write_text("secret-value-12345\n")
    setup = TestSetup(image="python:3.12-slim", test_command="cat data.txt")

    result = await run_in_sandbox(tmp_path, setup, timeout_seconds=60)

    assert result.passed
    assert "secret-value-12345" in result.output


async def test_run_in_sandbox_times_out_a_runaway_process(tmp_path: Path) -> None:
    setup = TestSetup(
        image="python:3.12-slim", test_command="python -c 'import time; time.sleep(30)'"
    )

    result = await run_in_sandbox(tmp_path, setup, timeout_seconds=2)

    assert result.timed_out
    assert result.exit_code is None
    assert not result.passed


async def test_run_in_sandbox_runs_a_real_pytest_suite(tmp_path: Path) -> None:
    """End-to-end proof that install_command + test_command actually work
    together as one shell invocation against a real, minimal test suite."""
    (tmp_path / "test_math.py").write_text("def test_addition():\n    assert 1 + 1 == 2\n")
    setup = TestSetup(
        image="python:3.12-slim",
        install_command="pip install --quiet pytest",
        test_command="pytest -v",
    )

    result = await run_in_sandbox(tmp_path, setup, timeout_seconds=120)

    assert result.passed
    assert "1 passed" in result.output


async def test_run_in_sandbox_raises_for_an_unknown_image(tmp_path: Path) -> None:
    setup = TestSetup(image="this-image-does-not-exist-codepilot-test:latest", test_command="true")

    with pytest.raises(SandboxError):
        await run_in_sandbox(tmp_path, setup, timeout_seconds=30)
