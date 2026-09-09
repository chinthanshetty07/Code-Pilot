import uuid

from app.core.db import async_session_factory
from app.models.repository import Repository
from app.services.indexing import index_repository


async def index_repository_task(ctx: dict, repository_id: str) -> dict:
    async with async_session_factory() as db:
        repository = await db.get(Repository, uuid.UUID(repository_id))
        if repository is None:
            return {"status": "error", "message": "repository not found"}

        await index_repository(db, repository)
        return {
            "status": repository.indexing_status,
            "file_count": repository.file_count,
            "chunk_count": repository.chunk_count,
        }
