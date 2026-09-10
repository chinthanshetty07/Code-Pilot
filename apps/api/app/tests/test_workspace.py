import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.services.workspace import Workspace, WorkspaceError


@pytest.fixture
async def workspace() -> AsyncIterator[Workspace]:
    root = Path(tempfile.mkdtemp(prefix="workspace-test-"))
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def greet(name):\n    return f'hello {name}'\n")
    (root / "README.md").write_text("# Demo\n")

    ws = Workspace(root)
    await ws._run_git("init", "-q")
    await ws._run_git("config", "user.email", "test@localhost")
    await ws._run_git("config", "user.name", "Test")
    await ws._run_git("add", "-A")
    await ws._run_git("commit", "-q", "-m", "Initial state")
    yield ws
    ws.cleanup()


def test_read_file_returns_content(workspace: Workspace) -> None:
    assert "def greet" in workspace.read_file("src/app.py")


def test_read_file_missing_raises(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceError, match="No such file"):
        workspace.read_file("nope.py")


@pytest.mark.parametrize(
    "bad_path", ["../../etc/passwd", "/etc/passwd", "src/../../outside.py", "..", ".."]
)
def test_resolve_rejects_paths_that_escape_the_workspace(
    workspace: Workspace, bad_path: str
) -> None:
    with pytest.raises(WorkspaceError, match="escapes the workspace"):
        workspace.resolve(bad_path)


def test_resolve_handles_macos_tmp_symlink_correctly(workspace: Workspace) -> None:
    """Regression test for a real bug hit during development: on macOS,
    tempfile.mkdtemp() returns a path through /tmp, itself a symlink to
    /private/tmp. Comparing a symlink-resolved candidate against an
    unresolved root rejected every valid path -- fixed by resolving root
    once at construction time instead of leaving it as given."""
    resolved = workspace.resolve("src/app.py")
    assert resolved.is_file()


def test_edit_file_replaces_unique_match(workspace: Workspace) -> None:
    workspace.edit_file("src/app.py", "hello {name}", "hi there, {name}")
    assert "hi there, {name}" in workspace.read_file("src/app.py")


def test_edit_file_missing_old_string_raises(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceError, match="not found"):
        workspace.edit_file("src/app.py", "this text is not in the file", "x")


def test_edit_file_non_unique_old_string_raises(workspace: Workspace) -> None:
    workspace.create_file("src/dup.py", "x = 1\nx = 1\n")
    with pytest.raises(WorkspaceError, match="not unique"):
        workspace.edit_file("src/dup.py", "x = 1", "x = 2")


def test_edit_file_missing_file_raises(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceError, match="No such file"):
        workspace.edit_file("nope.py", "a", "b")


def test_create_file_writes_new_file(workspace: Workspace) -> None:
    workspace.create_file("src/new_module.py", "VALUE = 42\n")
    assert workspace.read_file("src/new_module.py") == "VALUE = 42\n"


def test_create_file_existing_file_raises(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceError, match="already exists"):
        workspace.create_file("src/app.py", "overwrite")


def test_create_file_makes_parent_directories(workspace: Workspace) -> None:
    workspace.create_file("a/b/c/deep.py", "x = 1\n")
    assert workspace.read_file("a/b/c/deep.py") == "x = 1\n"


async def test_git_diff_reflects_all_changes(workspace: Workspace) -> None:
    workspace.edit_file("src/app.py", "hello {name}", "hi {name}")
    workspace.create_file("src/new_module.py", "VALUE = 42\n")

    diff = await workspace.git_diff()

    assert "src/app.py" in diff
    assert "src/new_module.py" in diff
    assert "-    return f'hello {name}'" in diff
    assert "+    return f'hi {name}'" in diff
    assert "+VALUE = 42" in diff


async def test_git_diff_is_empty_with_no_changes(workspace: Workspace) -> None:
    diff = await workspace.git_diff()
    assert diff.strip() == ""


def test_read_file_truncates_very_large_files(workspace: Workspace) -> None:
    from app.services.workspace import MAX_READ_FILE_CHARS

    workspace.create_file("big.txt", "x" * (MAX_READ_FILE_CHARS + 1000))

    content = workspace.read_file("big.txt")

    assert len(content) < MAX_READ_FILE_CHARS + 1000
    assert "truncated" in content


async def test_apply_diff_reproduces_the_edits_in_a_fresh_workspace(
    workspace: Workspace,
) -> None:
    """This is the exact mechanism the test runner (Milestone 7) relies on:
    a diff captured from one workspace must apply cleanly to a second,
    independently-created workspace starting from the identical initial
    state -- since the original workspace is long gone by the time tests
    run (see Workspace.apply_diff's docstring)."""
    workspace.edit_file("src/app.py", "hello {name}", "hi there, {name}")
    workspace.create_file("src/new_module.py", "VALUE = 42\n")
    diff = await workspace.git_diff()

    fresh_root = Path(tempfile.mkdtemp(prefix="workspace-test-fresh-"))
    (fresh_root / "src").mkdir()
    (fresh_root / "src" / "app.py").write_text("def greet(name):\n    return f'hello {name}'\n")
    (fresh_root / "README.md").write_text("# Demo\n")
    fresh = Workspace(fresh_root)
    await fresh._run_git("init", "-q")
    await fresh._run_git("config", "user.email", "test@localhost")
    await fresh._run_git("config", "user.name", "Test")
    await fresh._run_git("add", "-A")
    await fresh._run_git("commit", "-q", "-m", "Initial state")

    await fresh.apply_diff(diff)

    assert "hi there, {name}" in fresh.read_file("src/app.py")
    assert fresh.read_file("src/new_module.py") == "VALUE = 42\n"
    fresh.cleanup()


async def test_apply_diff_raises_when_it_does_not_match(workspace: Workspace) -> None:
    bogus_diff = (
        "diff --git a/src/app.py b/src/app.py\n"
        "index 0000000..1111111 100644\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def greet(name):\n"
        "-    return f'this text does not exist in the real file'\n"
        "+    return f'x'\n"
    )
    with pytest.raises(WorkspaceError, match="git apply .* failed"):
        await workspace.apply_diff(bogus_diff)
