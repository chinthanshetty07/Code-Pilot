import asyncio
import random
from typing import Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.models.code_chunk import EMBEDDING_DIMENSIONS

_BATCH_SIZE = 100


class EmbeddingProvider(Protocol):
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single *search query* string.

        Separate from `embed` because some providers use an asymmetric model
        that embeds queries differently from the documents being searched
        (see `GeminiEmbeddingProvider`) -- using the wrong one for a query
        still returns a same-shaped vector, so this isn't something that
        fails loudly, it just quietly makes search results worse.
        """
        ...


class OpenAIEmbeddingProvider:
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            response = await self._client.embeddings.create(input=batch, model=self._model)
            results.extend(item.embedding for item in response.data)
        return results

    async def embed_query(self, text: str) -> list[float]:
        # text-embedding-3-small is symmetric -- no separate query mode.
        (embedding,) = await self.embed([text])
        return embedding


# Unlike OpenAI, Gemini's embedding models take exactly one text per
# request -- there is no `input: list[str]` batch parameter (confirmed
# against the installed `google-genai` SDK: `contents` fans out to multiple
# *candidates* for other content types, not a batch of independent
# embeddings). So instead of one fat request per batch, this fires many
# small requests under a concurrency cap, retrying on 429s -- the free tier
# is rate- rather than token-limited, and a whole-repo indexing run can
# easily be thousands of chunks.
_MAX_CONCURRENT_REQUESTS = 5
_MAX_RETRIES = 5
_RETRY_BASE_DELAY_SECONDS = 1.0


class GeminiEmbeddingProvider:
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)

    async def _embed_one(self, text: str, task_type: str) -> list[float]:
        config = types.EmbedContentConfig(
            output_dimensionality=self.dimensions,
            task_type=task_type,
        )
        async with self._semaphore:
            for attempt in range(_MAX_RETRIES):
                try:
                    response = await self._client.aio.models.embed_content(
                        model=self._model, contents=text, config=config
                    )
                    return response.embeddings[0].values
                except genai_errors.APIError as exc:
                    if exc.code != 429 or attempt == _MAX_RETRIES - 1:
                        raise
                    delay = _RETRY_BASE_DELAY_SECONDS * (2**attempt) + random.random()
                    await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # loop always returns or raises

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.gather(
            *(self._embed_one(text, "RETRIEVAL_DOCUMENT") for text in texts)
        )

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed_one(text, "RETRIEVAL_QUERY")


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if not settings.embedding_api_key:
        raise ValueError("EMBEDDING_API_KEY is not configured")
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddingProvider(settings.embedding_api_key, settings.embedding_model)
    if settings.embedding_provider == "gemini":
        return GeminiEmbeddingProvider(settings.embedding_api_key, settings.embedding_model)
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")
