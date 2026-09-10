"""Pushes a CodeChange's diff to a real branch on GitHub and opens a pull
request for it -- the "Git Branch/Commit -> GitHub Pull Request" tail end
of the pipeline, triggered by the human's own explicit approval (see
app/api/issues.py's create_pull_request endpoint; there's no separate
"approved" state to track, the click that creates the PR *is* the
approval). No LLM is involved, so this lives alongside test_runner.py
rather than in app/agents/ -- it's a deterministic sequence of git/API
calls, not a model making judgment calls.
"""

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.github.client import GitHubClient, build_authenticated_remote_url
from app.models.pull_request import PullRequest
from app.services.github_accounts import get_access_token
from app.services.workspace import Workspace, create_workspace

logger = logging.getLogger(__name__)

# GitHub's own suggested subject-line length for a PR/commit title -- a
# longer one just gets visually truncated in GitHub's own UI anyway, so
# it's capped here instead of leaving an already-illegible cutoff there.
PR_TITLE_MAX_CHARS = 72
# How much of the issue description feeds the branch-name slug -- long
# enough to stay recognizable, short enough that branch names don't get
# unwieldy.
BRANCH_SLUG_MAX_CHARS = 40

_SLUG_UNSAFE_CHARS = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_chars: int) -> str:
    slug = _SLUG_UNSAFE_CHARS.sub("-", text.lower()).strip("-")
    return slug[:max_chars].rstrip("-")


def _branch_name_for_issue(issue) -> str:
    """Deterministic from issue.id -- computed fresh on every attempt
    (never persisted-then-trusted) so a retry after a failed push targets
    the exact same branch name rather than accumulating an abandoned one
    per attempt. Re-pushing the same (fast-forward) commit to a branch
    that already exists from a previous attempt is harmless."""
    slug = _slugify(issue.description, BRANCH_SLUG_MAX_CHARS) or "change"
    return f"codepilot/{slug}-{issue.id.hex[:8]}"


def _pr_title(issue, code_change) -> str:
    source = code_change.summary or issue.description
    first_line = source.strip().split("\n")[0]
    first_sentence = first_line.split(". ")[0].strip()
    title = first_sentence or "CodePilot: automated change"
    if len(title) > PR_TITLE_MAX_CHARS:
        title = title[: PR_TITLE_MAX_CHARS - 1].rstrip() + "…"
    return title


def _pr_body(issue, plan, code_change, test_run, review) -> str:
    sections = [
        f"## Summary\n{code_change.summary or '(no summary)'}",
        f"## Issue\n{issue.description}",
    ]
    if plan is not None:
        sections.append(f"## Plan\n{plan.summary}")
    if test_run is not None and test_run.status == "passed":
        attempts_note = (
            f" (after {test_run.fix_attempts} automatic fix "
            f"attempt{'s' if test_run.fix_attempts != 1 else ''} by the Coder agent)"
            if test_run.fix_attempts > 0
            else ""
        )
        command_note = f": `{test_run.command}`" if test_run.command else ""
        sections.append(f"## Tests\nPassed{attempts_note}{command_note}")
    if review is not None and review.status in ("approved", "changes_requested"):
        verdict = "Approved" if review.status == "approved" else "Changes requested"
        review_section = f"## AI review\n**{verdict}** -- {review.summary}"
        if review.comments:
            review_section += "\n\n" + "\n".join(
                f"- **{'Blocking' if c['severity'] == 'blocking' else 'Suggestion'}** "
                f"(`{c['file_path']}`): {c['comment']}"
                for c in review.comments
            )
        sections.append(review_section)
    sections.append("---\n*Opened automatically by CodePilot from a developer issue.*")
    return "\n\n".join(sections)


async def create_pull_request(db: AsyncSession, pull_request: PullRequest) -> None:
    """Reconstructs the Coder agent's edited state in a fresh workspace
    (same apply_diff mechanism as the test runner and reviewer), commits
    it for real, pushes it to a new branch on GitHub, and opens a pull
    request. Never raises -- failures are recorded on the pull_request
    itself (status="failed"), mirroring the rest of the pipeline so the
    job always completes and the failure is visible to the user."""
    pull_request.status = "creating"
    pull_request.error = None
    await db.commit()

    code_change = pull_request.code_change
    issue = code_change.issue
    plan = issue.plan
    repository = issue.repository
    workspace: Workspace | None = None

    try:
        if not code_change.diff or not code_change.diff.strip():
            raise ValueError("This code change has no diff to open a pull request for")

        branch_name = _branch_name_for_issue(issue)
        pull_request.branch_name = branch_name
        await db.commit()

        access_token = await get_access_token(db, repository.owner_id)
        workspace = await create_workspace(access_token, repository.full_name)
        await workspace.apply_diff(code_change.diff)
        await workspace.commit_all(_pr_title(issue, code_change))

        remote_url = build_authenticated_remote_url(access_token, repository.full_name)
        await workspace.push_branch(remote_url, branch_name)

        client = GitHubClient(access_token)
        try:
            pr_data = await client.create_pull_request(
                repository.full_name,
                title=_pr_title(issue, code_change),
                body=_pr_body(issue, plan, code_change, code_change.test_run, code_change.review),
                head=branch_name,
                base=repository.default_branch,
            )
        finally:
            await client.aclose()

        pull_request.status = "created"
        pull_request.pr_number = pr_data["number"]
        pull_request.pr_url = pr_data["html_url"]
        await db.commit()

    except Exception as exc:
        logger.exception("Pull request creation failed for code_change %s", code_change.id)
        await db.rollback()
        pull_request.status = "failed"
        pull_request.error = str(exc)[:500]
        await db.commit()
    finally:
        if workspace is not None:
            workspace.cleanup()
