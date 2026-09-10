import asyncio
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import delete, select

from app.api import issues as issues_module
from app.core.db import async_session_factory
from app.core.redis import redis_client
from app.core.sessions import SESSION_COOKIE_NAME
from app.main import app
from app.models.code_change import CodeChange
from app.models.issue import Issue
from app.models.repository import Repository
from app.models.user import User

# Deliberately plain async tests + httpx.AsyncClient(ASGITransport), not
# FastAPI's TestClient -- TestClient owns its own portal/event loop, and a
# *second* file with its own module-scoped TestClient (test_auth.py already
# has one) reliably breaks: the module-level redis_client/engine singletons
# bind to whichever loop touches them first, and once that file's portal
# closes at teardown, a second file's fresh portal/loop can't reuse them
# (confirmed empirically -- "RuntimeError: Event loop is closed"). Every
# other async-only test file in this suite (test_pull_requests.py,
# test_reviewer.py, etc.) already avoids TestClient for the same underlying
# reason; this file follows that majority convention instead of adding a
# second TestClient user.


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _fake_arq_pool(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    # None of these tests should enqueue a real background job -- the real
    # worker container is a live process sharing this same Redis, and would
    # otherwise pick up a real "create_plan_task"/"create_code_change_task"
    # job for a repository that doesn't exist on GitHub.
    pool = AsyncMock()
    monkeypatch.setattr(issues_module, "get_arq_pool", AsyncMock(return_value=pool))
    return pool


async def _create_user_and_session() -> tuple[uuid.UUID, str]:
    async with async_session_factory() as session:
        user = User(username=f"issues-api-test-{uuid.uuid4().hex[:8]}")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    session_id = f"issues-api-test-session-{uuid.uuid4().hex}"
    await redis_client.setex(f"session:{session_id}", 60, str(user.id))
    return user.id, session_id


async def _create_repo_and_issue(
    owner_id: uuid.UUID, planning_status: str
) -> tuple[uuid.UUID, uuid.UUID]:
    async with async_session_factory() as db:
        repo = Repository(
            owner_id=owner_id,
            github_id=-(uuid.uuid4().int % 1_000_000),
            full_name="api-test/repo",
            default_branch="main",
        )
        db.add(repo)
        await db.flush()
        issue = Issue(
            repository_id=repo.id,
            created_by_id=owner_id,
            description="Test issue for the HTTP route layer.",
            planning_status=planning_status,
        )
        db.add(issue)
        await db.commit()
        return repo.id, issue.id


async def _cleanup(user_id: uuid.UUID, repo_id: uuid.UUID, issue_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        await db.execute(delete(CodeChange).where(CodeChange.issue_id == issue_id))
        await db.execute(delete(Issue).where(Issue.id == issue_id))
        await db.execute(delete(Repository).where(Repository.id == repo_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


async def _delete_user(user_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


async def test_get_issue_requires_authentication() -> None:
    async with _client() as ac:
        response = await ac.get(f"/api/repositories/{uuid.uuid4()}/issues/{uuid.uuid4()}")

    assert response.status_code == 401


async def test_get_issue_404s_for_a_wellformed_but_nonexistent_id() -> None:
    user_id, session_id = await _create_user_and_session()
    repo_id, issue_id = await _create_repo_and_issue(user_id, "planned")
    try:
        async with _client() as ac:
            response = await ac.get(
                f"/api/repositories/{repo_id}/issues/{uuid.uuid4()}",
                cookies={SESSION_COOKIE_NAME: session_id},
            )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "HTTP_404"
    finally:
        await _cleanup(user_id, repo_id, issue_id)


async def test_get_issue_422s_for_a_malformed_id_instead_of_crashing() -> None:
    # Regression test: repository_id/issue_id used to be typed `str` on
    # these routes, so a non-UUID path segment reached the database as a
    # raw string and crashed with an unhandled 500 (invalid input syntax
    # for type uuid). Typing them `uuid.UUID` lets FastAPI reject it before
    # the handler ever runs.
    user_id, session_id = await _create_user_and_session()
    repo_id, issue_id = await _create_repo_and_issue(user_id, "planned")
    try:
        async with _client() as ac:
            response = await ac.get(
                f"/api/repositories/{repo_id}/issues/not-a-real-uuid",
                cookies={SESSION_COOKIE_NAME: session_id},
            )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "HTTP_422"
    finally:
        await _cleanup(user_id, repo_id, issue_id)


async def test_get_issue_404s_for_another_users_issue() -> None:
    owner_id, _owner_session = await _create_user_and_session()
    repo_id, issue_id = await _create_repo_and_issue(owner_id, "planned")
    other_user_id, other_session_id = await _create_user_and_session()
    try:
        async with _client() as ac:
            response = await ac.get(
                f"/api/repositories/{repo_id}/issues/{issue_id}",
                cookies={SESSION_COOKIE_NAME: other_session_id},
            )

        # Folded into the same 404 as "doesn't exist" -- not a 403 -- so a
        # response never leaks whether a given id belongs to someone else.
        assert response.status_code == 404
    finally:
        await _cleanup(owner_id, repo_id, issue_id)
        await _delete_user(other_user_id)


async def test_create_code_change_409s_when_plan_is_not_ready() -> None:
    user_id, session_id = await _create_user_and_session()
    repo_id, issue_id = await _create_repo_and_issue(user_id, "queued")
    try:
        async with _client() as ac:
            response = await ac.post(
                f"/api/repositories/{repo_id}/issues/{issue_id}/code-changes",
                cookies={SESSION_COOKIE_NAME: session_id},
            )

        assert response.status_code == 409
        assert "plan" in response.json()["error"]["message"].lower()
    finally:
        await _cleanup(user_id, repo_id, issue_id)


async def test_create_code_change_succeeds_and_serializes_a_fresh_response() -> None:
    # Regression test: a brand new CodeChange's test_run/review/pull_request
    # relationships were never selectinload'ed (there was no code_change row
    # to load them onto before this request), so building the IssueOut
    # response used to crash with SQLAlchemy's MissingGreenlet -- an
    # async-unsafe lazy load triggered by pydantic serialization running
    # outside an awaited context. See the fix's comment in
    # app/api/issues.py::create_code_change.
    user_id, session_id = await _create_user_and_session()
    repo_id, issue_id = await _create_repo_and_issue(user_id, "planned")
    try:
        async with _client() as ac:
            response = await ac.post(
                f"/api/repositories/{repo_id}/issues/{issue_id}/code-changes",
                cookies={SESSION_COOKIE_NAME: session_id},
            )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["code_change"]["generation_status"] == "queued"
        assert body["code_change"]["test_run"] is None
        assert body["code_change"]["review"] is None
        assert body["code_change"]["pull_request"] is None
    finally:
        await _cleanup(user_id, repo_id, issue_id)


async def test_create_code_change_concurrent_requests_do_not_duplicate(
    _fake_arq_pool: AsyncMock,
) -> None:
    """Proves the row-lock fix for the check-then-write race in
    _get_owned_issue(for_update=True): firing two real concurrent HTTP
    requests at the same freshly-planned issue (no code_change yet) must
    produce exactly one 201 and one clean 409 -- never two 201s, and never
    an unhandled 500 from a UNIQUE constraint violation racing underneath
    the "is generation already in progress" status check."""
    user_id, session_id = await _create_user_and_session()
    repo_id, issue_id = await _create_repo_and_issue(user_id, "planned")
    try:
        url = f"/api/repositories/{repo_id}/issues/{issue_id}/code-changes"
        cookies = {SESSION_COOKIE_NAME: session_id}
        async with _client() as ac:
            responses = await asyncio.gather(
                ac.post(url, cookies=cookies),
                ac.post(url, cookies=cookies),
            )

        statuses = sorted(r.status_code for r in responses)
        assert statuses == [201, 409], [r.text for r in responses]

        async with async_session_factory() as db:
            result = await db.execute(select(CodeChange).where(CodeChange.issue_id == issue_id))
            assert len(result.scalars().all()) == 1
    finally:
        await _cleanup(user_id, repo_id, issue_id)
