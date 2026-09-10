"""A local, git-backed checkout of a repository for the Coder agent's file
tools to operate on. Downloads the same tarball indexing uses, extracts it
into a fresh temp directory, and commits the initial state so a later
git diff shows exactly what the agent changed -- no more, no less.

Unlike app/rag/chunking.py and app/services/search.py, nothing here reads
from or writes to the database; a Workspace is purely local filesystem +
subprocess state, scoped to one coding run and discarded afterward.
"""

import asyncio
import io
import shutil
import tarfile
import tempfile
from pathlib import Path

from app.github.client import GitHubClient
from app.rag.ignore_patterns import should_ignore_path

# Content returned to the LLM from read_file beyond this is truncated with a
# note -- a whole giant file blowing out the context window is a worse
# outcome than the agent needing to re-read a narrower slice of it.
MAX_READ_FILE_CHARS = 50_000


class WorkspaceError(Exception):
    """Raised for workspace operation failures (file not found, old_string
    not found/unique, a path attempting to escape the workspace, a git
    command failing, ...). Caught by the Coder agent's tool loop and fed
    back to the model as a tool result, the same way a bad search_code
    query doesn't crash the Planner -- most of these are exactly the kind
    of thing a model can recover from if told clearly what went wrong."""


class Workspace:
    def __init__(self, root: Path) -> None:
        # Resolved once here (rather than per-call in resolve()) so root and
        # candidate are always compared on equal footing -- on macOS in
        # particular, tempfile.mkdtemp() can return a path through /tmp,
        # itself a symlink to /private/tmp, and comparing an unresolved
        # root against a resolved candidate rejects every valid path.
        self.root = root.resolve()

    def resolve(self, relative_path: str) -> Path:
        """Resolves `relative_path` within the workspace root, rejecting
        any attempt to escape it (`../../etc/passwd`, an absolute path,
        ...) -- the agent's tools must never be able to touch anything
        outside the checkout."""
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise WorkspaceError(f"Path escapes the workspace: {relative_path!r}") from None
        return candidate

    def read_file(self, relative_path: str) -> str:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise WorkspaceError(f"No such file: {relative_path!r}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise WorkspaceError(f"{relative_path!r} is not a UTF-8 text file") from None
        if len(content) > MAX_READ_FILE_CHARS:
            content = (
                content[:MAX_READ_FILE_CHARS]
                + f"\n\n[... truncated, {len(content) - MAX_READ_FILE_CHARS} more characters. "
                "Use edit_file's old_string against a smaller, specific section instead of "
                "trying to read the whole file.]"
            )
        return content

    def create_file(self, relative_path: str, content: str) -> None:
        path = self.resolve(relative_path)
        if path.exists():
            raise WorkspaceError(f"{relative_path!r} already exists -- use edit_file instead")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def edit_file(self, relative_path: str, old_string: str, new_string: str) -> None:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise WorkspaceError(f"No such file: {relative_path!r}")
        content = path.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            raise WorkspaceError(f"old_string not found in {relative_path!r}")
        if count > 1:
            raise WorkspaceError(
                f"old_string is not unique in {relative_path!r} ({count} matches) -- "
                "include more surrounding context so it matches exactly one place"
            )
        path.write_text(content.replace(old_string, new_string), encoding="utf-8")

    async def _run_git(self, *args: str, input_text: str | None = None) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=self.root,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdin_bytes = input_text.encode("utf-8") if input_text is not None else None
        stdout, stderr = await process.communicate(input=stdin_bytes)
        if process.returncode != 0:
            raise WorkspaceError(f"git {' '.join(args)} failed: {stderr.decode(errors='replace')}")
        return stdout.decode(errors="replace")

    async def apply_diff(self, diff: str) -> None:
        """Applies a unified diff (as produced by git_diff) to the working
        tree -- used to reconstruct a Coder agent run's edited state in a
        fresh workspace later (e.g. for the test runner), since the
        original workspace is ephemeral and cleaned up right after its own
        job finishes. Reconstructing from the stored diff rather than
        keeping the original workspace around works correctly regardless of
        how much later, or on which worker process, that happens."""
        await self._run_git("apply", "-", input_text=diff)

    async def git_diff(self) -> str:
        """Stages everything (including new/deleted files) and diffs
        against the initial commit -- the working tree itself is only ever
        touched by this workspace's own tools, so this always reflects
        exactly the agent's own edits."""
        await self._run_git("add", "-A")
        return await self._run_git("diff", "--cached")

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


async def create_workspace(access_token: str, full_name: str, ref: str = "HEAD") -> Workspace:
    client = GitHubClient(access_token)
    try:
        tarball = await client.download_tarball(full_name, ref)
    finally:
        await client.aclose()

    root = Path(tempfile.mkdtemp(prefix="codepilot-workspace-"))
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # GitHub tarballs wrap everything in a single "owner-repo-sha/" dir.
            parts = member.name.split("/", 1)
            relative_path = parts[1] if len(parts) == 2 else parts[0]
            if not relative_path or should_ignore_path(relative_path):
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(extracted.read())

    workspace = Workspace(root)
    await workspace._run_git("init", "-q")
    await workspace._run_git("config", "user.email", "codepilot@localhost")
    await workspace._run_git("config", "user.name", "CodePilot")
    await workspace._run_git("add", "-A")
    await workspace._run_git("commit", "-q", "-m", "Initial state", "--allow-empty")
    return workspace
