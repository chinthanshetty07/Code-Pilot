import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import delete

from app.core.db import async_session_factory
from app.models.code_chunk import EMBEDDING_DIMENSIONS, CodeChunk
from app.models.repository import Repository
from app.models.user import User
from app.services import search as search_module
from app.services.search import search_code


def _vector(hot_index: int, value: float = 1.0) -> list[float]:
    """A mostly-zero vector with one component set -- lets a test assert
    exactly which chunk a query should rank first without needing real
    embeddings."""
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[hot_index] = value
    return values


@dataclass
class _Repo:
    id: uuid.UUID
    owner_id: uuid.UUID


@pytest.fixture
async def two_repos_with_chunks() -> AsyncIterator[tuple[_Repo, _Repo]]:
    async with async_session_factory() as db:
        owner = User(username=f"search-test-{uuid.uuid4().hex[:8]}")
        db.add(owner)
        await db.flush()

        repo_a = Repository(
            owner_id=owner.id, github_id=-1, full_name="search-test/repo-a", default_branch="main"
        )
        repo_b = Repository(
            owner_id=owner.id, github_id=-2, full_name="search-test/repo-b", default_branch="main"
        )
        db.add_all([repo_a, repo_b])
        await db.flush()

        # repo_a: one chunk "aligned" with axis 0, one with axis 1.
        # repo_b: one chunk aligned with axis 0 too -- must never show up in
        # a repo_a search, no matter how well it matches the query.
        db.add_all(
            [
                CodeChunk(
                    repository_id=repo_a.id,
                    file_path="a/login.py",
                    language="python",
                    chunk_type="function",
                    symbol_name="login",
                    start_line=1,
                    end_line=5,
                    content="def login(): ...",
                    embedding=_vector(0),
                ),
                CodeChunk(
                    repository_id=repo_a.id,
                    file_path="a/invoice.py",
                    language="python",
                    chunk_type="function",
                    symbol_name="calculate_invoice",
                    start_line=1,
                    end_line=5,
                    content="def calculate_invoice(): ...",
                    embedding=_vector(1),
                ),
                CodeChunk(
                    repository_id=repo_b.id,
                    file_path="b/other_login.py",
                    language="python",
                    chunk_type="function",
                    symbol_name="other_login",
                    start_line=1,
                    end_line=5,
                    content="def other_login(): ...",
                    embedding=_vector(0),
                ),
            ]
        )
        await db.commit()

        try:
            yield (
                _Repo(id=repo_a.id, owner_id=owner.id),
                _Repo(id=repo_b.id, owner_id=owner.id),
            )
        finally:
            async with async_session_factory() as cleanup_db:
                await cleanup_db.execute(
                    delete(CodeChunk).where(CodeChunk.repository_id.in_([repo_a.id, repo_b.id]))
                )
                await cleanup_db.execute(
                    delete(Repository).where(Repository.id.in_([repo_a.id, repo_b.id]))
                )
                await cleanup_db.execute(delete(User).where(User.id == owner.id))
                await cleanup_db.commit()


class _FakeProvider:
    def __init__(self, query_vector: list[float]) -> None:
        self.dimensions = EMBEDDING_DIMENSIONS
        self._query_vector = query_vector
        self.embed_query_calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("search should never call embed(), only embed_query()")

    async def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls += 1
        return self._query_vector


def _patch_provider(monkeypatch: pytest.MonkeyPatch, query_vector: list[float]) -> _FakeProvider:
    fake = _FakeProvider(query_vector)
    monkeypatch.setattr(search_module, "get_embedding_provider", lambda: fake)
    return fake


async def test_search_ranks_closest_chunk_first(
    monkeypatch: pytest.MonkeyPatch, two_repos_with_chunks: tuple[_Repo, _Repo]
) -> None:
    repo_a, _repo_b = two_repos_with_chunks
    _patch_provider(monkeypatch, _vector(0))

    async with async_session_factory() as db:
        results = await search_code(db, repo_a.id, "how do users log in")

    assert [r.symbol_name for r in results] == ["login", "calculate_invoice"]
    assert results[0].score > results[1].score
    assert results[0].score == pytest.approx(1.0, abs=1e-6)


async def test_search_is_scoped_to_the_given_repository(
    monkeypatch: pytest.MonkeyPatch, two_repos_with_chunks: tuple[_Repo, _Repo]
) -> None:
    repo_a, _repo_b = two_repos_with_chunks
    _patch_provider(monkeypatch, _vector(0))

    async with async_session_factory() as db:
        results = await search_code(db, repo_a.id, "login")

    assert all(r.file_path.startswith("a/") for r in results)
    assert "other_login" not in [r.symbol_name for r in results]


async def test_search_respects_limit(
    monkeypatch: pytest.MonkeyPatch, two_repos_with_chunks: tuple[_Repo, _Repo]
) -> None:
    repo_a, _repo_b = two_repos_with_chunks
    _patch_provider(monkeypatch, _vector(0))

    async with async_session_factory() as db:
        results = await search_code(db, repo_a.id, "login", limit=1)

    assert len(results) == 1
    assert results[0].symbol_name == "login"


async def test_search_clamps_limit_above_max(
    monkeypatch: pytest.MonkeyPatch, two_repos_with_chunks: tuple[_Repo, _Repo]
) -> None:
    repo_a, _repo_b = two_repos_with_chunks
    _patch_provider(monkeypatch, _vector(0))

    async with async_session_factory() as db:
        # Must not raise, and must not attempt to return more than exist.
        results = await search_code(db, repo_a.id, "login", limit=10_000)

    assert len(results) == 2


async def test_search_blank_query_returns_no_results_without_calling_provider(
    monkeypatch: pytest.MonkeyPatch, two_repos_with_chunks: tuple[_Repo, _Repo]
) -> None:
    repo_a, _repo_b = two_repos_with_chunks
    fake = _patch_provider(monkeypatch, _vector(0))

    async with async_session_factory() as db:
        results = await search_code(db, repo_a.id, "   ")

    assert results == []
    assert fake.embed_query_calls == 0
