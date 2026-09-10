import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.db import async_session_factory
from app.core.redis import redis_client
from app.core.sessions import SESSION_COOKIE_NAME
from app.main import app
from app.models.user import User


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    # Module-scoped: one portal/event loop for the whole file, so the
    # module-level engine/redis_client singletons (bound to whichever loop
    # first touches them) aren't reused across a *different*, already-closed
    # loop from an earlier test's own TestClient instance.
    with TestClient(app) as c:
        yield c


async def _create_test_user() -> User:
    async with async_session_factory() as session:
        user = User(username=f"test-user-{uuid.uuid4().hex[:8]}")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_session_for(user_id: uuid.UUID) -> str:
    session_id = f"test-session-{uuid.uuid4().hex}"
    await redis_client.setex(f"session:{session_id}", 60, str(user_id))
    return session_id


def test_auth_me_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "HTTP_401"


def test_auth_me_returns_current_user(client: TestClient) -> None:
    user = client.portal.call(_create_test_user)
    session_id = client.portal.call(_create_session_for, user.id)

    response = client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: session_id})

    assert response.status_code == 200
    assert response.json()["username"] == user.username


def test_logout_clears_session(client: TestClient) -> None:
    user = client.portal.call(_create_test_user)
    session_id = client.portal.call(_create_session_for, user.id)

    logout_response = client.post("/api/auth/logout", cookies={SESSION_COOKIE_NAME: session_id})
    assert logout_response.status_code == 204

    me_response = client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: session_id})
    assert me_response.status_code == 401


def test_repositories_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/repositories")

    assert response.status_code == 401


def test_github_repos_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/github/repos")

    assert response.status_code == 401


def test_index_repository_requires_authentication(client: TestClient) -> None:
    response = client.post("/api/repositories/does-not-matter/index")

    assert response.status_code == 401


def test_search_repository_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/repositories/does-not-matter/search?q=test")

    assert response.status_code == 401


def test_create_issue_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/api/repositories/does-not-matter/issues", json={"description": "fix it"}
    )

    assert response.status_code == 401


def test_list_issues_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/repositories/does-not-matter/issues")

    assert response.status_code == 401


def test_create_code_change_requires_authentication(client: TestClient) -> None:
    response = client.post("/api/repositories/does-not-matter/issues/does-not-matter/code-changes")

    assert response.status_code == 401
