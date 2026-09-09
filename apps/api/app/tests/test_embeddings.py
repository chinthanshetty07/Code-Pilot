from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import errors

from app.rag.embeddings import GeminiEmbeddingProvider


def _fake_response(values: list[float]) -> MagicMock:
    embedding = MagicMock()
    embedding.values = values
    response = MagicMock()
    response.embeddings = [embedding]
    return response


def _rate_limit_error() -> errors.APIError:
    return errors.APIError(code=429, response_json={"error": {"message": "rate limited"}})


def _server_error() -> errors.APIError:
    return errors.APIError(code=500, response_json={"error": {"message": "boom"}})


async def test_embed_empty_list_makes_no_requests() -> None:
    provider = GeminiEmbeddingProvider(api_key="fake", model="gemini-embedding-001")
    provider._client.aio.models.embed_content = AsyncMock()

    result = await provider.embed([])

    assert result == []
    provider._client.aio.models.embed_content.assert_not_awaited()


async def test_embed_single_text_returns_its_vector() -> None:
    provider = GeminiEmbeddingProvider(api_key="fake", model="gemini-embedding-001")
    provider._client.aio.models.embed_content = AsyncMock(
        return_value=_fake_response([0.1, 0.2, 0.3])
    )

    result = await provider.embed(["hello world"])

    assert result == [[0.1, 0.2, 0.3]]
    call_kwargs = provider._client.aio.models.embed_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-embedding-001"
    assert call_kwargs["contents"] == "hello world"
    assert call_kwargs["config"].output_dimensionality == provider.dimensions
    assert call_kwargs["config"].task_type == "RETRIEVAL_DOCUMENT"


async def test_embed_preserves_order_across_concurrent_requests() -> None:
    provider = GeminiEmbeddingProvider(api_key="fake", model="gemini-embedding-001")

    async def fake_embed_content(*, model: str, contents: str, config: object) -> MagicMock:
        # Reverse the natural completion order so gather() must be relying on
        # input order, not arrival order, to keep results aligned.
        if contents == "first":
            await __import__("asyncio").sleep(0.02)
        return _fake_response([float(len(contents))])

    provider._client.aio.models.embed_content = AsyncMock(side_effect=fake_embed_content)

    result = await provider.embed(["first", "second"])

    assert result == [[5.0], [6.0]]


async def test_embed_retries_on_rate_limit_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GeminiEmbeddingProvider(api_key="fake", model="gemini-embedding-001")
    provider._client.aio.models.embed_content = AsyncMock(
        side_effect=[_rate_limit_error(), _rate_limit_error(), _fake_response([1.0])]
    )
    monkeypatch.setattr("app.rag.embeddings.asyncio.sleep", AsyncMock(return_value=None))

    result = await provider.embed(["retry me"])

    assert result == [[1.0]]
    assert provider._client.aio.models.embed_content.await_count == 3


async def test_embed_does_not_retry_non_rate_limit_errors() -> None:
    provider = GeminiEmbeddingProvider(api_key="fake", model="gemini-embedding-001")
    provider._client.aio.models.embed_content = AsyncMock(side_effect=_server_error())

    with pytest.raises(errors.APIError) as exc_info:
        await provider.embed(["boom"])

    assert exc_info.value.code == 500
    assert provider._client.aio.models.embed_content.await_count == 1


async def test_embed_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.rag.embeddings as embeddings_module

    provider = GeminiEmbeddingProvider(api_key="fake", model="gemini-embedding-001")
    provider._client.aio.models.embed_content = AsyncMock(side_effect=_rate_limit_error())
    monkeypatch.setattr("app.rag.embeddings.asyncio.sleep", AsyncMock(return_value=None))

    with pytest.raises(errors.APIError) as exc_info:
        await provider.embed(["always limited"])

    assert exc_info.value.code == 429
    assert provider._client.aio.models.embed_content.await_count == embeddings_module._MAX_RETRIES
