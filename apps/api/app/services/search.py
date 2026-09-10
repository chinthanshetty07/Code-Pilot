import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.code_chunk import CodeChunk
from app.rag.embeddings import get_embedding_provider

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


@dataclass
class SearchResult:
    chunk_id: uuid.UUID
    file_path: str
    language: str
    chunk_type: str
    symbol_name: str | None
    start_line: int
    end_line: int
    content: str
    score: float


async def search_code(
    db: AsyncSession,
    repository_id: uuid.UUID,
    query: str,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchResult]:
    """Semantic search over one repository's indexed chunks.

    Reusable on purpose: this is also the function the Planner/Coder agents
    call as their `search_code` tool starting Milestone 5/6, not just this
    milestone's HTTP endpoint -- so it validates its own inputs rather than
    trusting FastAPI's request validation to have already done it.
    """
    query = query.strip()
    if not query:
        return []
    limit = min(max(limit, 1), MAX_LIMIT)

    provider = get_embedding_provider()
    query_vector = await provider.embed_query(query)

    distance = CodeChunk.embedding.cosine_distance(query_vector)
    result = await db.execute(
        select(CodeChunk, distance.label("distance"))
        .where(CodeChunk.repository_id == repository_id)
        .order_by(distance)
        .limit(limit)
    )

    return [
        SearchResult(
            chunk_id=chunk.id,
            file_path=chunk.file_path,
            language=chunk.language,
            chunk_type=chunk.chunk_type,
            symbol_name=chunk.symbol_name,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            content=chunk.content,
            # Cosine distance is in [0, 2] in principle; clamp so a rare
            # negative-similarity match still renders as a sane 0% rather
            # than a confusing negative score in the UI.
            score=max(0.0, 1 - distance_value),
        )
        for chunk, distance_value in result.all()
    ]
