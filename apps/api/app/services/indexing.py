import io
import logging
import tarfile
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_token
from app.github.client import GitHubClient
from app.models.code_chunk import CodeChunk
from app.models.github_account import GitHubAccount
from app.models.repository import Repository
from app.rag.chunking import chunk_file, detect_language
from app.rag.embeddings import get_embedding_provider
from app.rag.ignore_patterns import MAX_FILE_SIZE_BYTES, should_ignore_path

logger = logging.getLogger(__name__)

MAX_FILES = 3000


def _extract_text_files(tarball: bytes) -> dict[str, str]:
    """Extract a GitHub tarball in-memory into {relative_path: content},
    skipping ignored paths, oversized files, and anything that isn't
    decodable UTF-8 text (binary files that slipped past extension
    filtering)."""
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or member.size > MAX_FILE_SIZE_BYTES:
                continue

            # GitHub tarballs wrap everything in a single "owner-repo-sha/" dir.
            parts = member.name.split("/", 1)
            relative_path = parts[1] if len(parts) == 2 else parts[0]
            if not relative_path or should_ignore_path(relative_path):
                continue

            extracted = tar.extractfile(member)
            if extracted is None:
                continue

            raw = extracted.read()
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if "\x00" in content:
                continue

            files[relative_path] = content
            if len(files) >= MAX_FILES:
                break

    return files


async def _get_access_token(db: AsyncSession, owner_id) -> str:
    result = await db.execute(select(GitHubAccount).where(GitHubAccount.user_id == owner_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("No GitHub account linked for this repository's owner")
    return decrypt_token(account.access_token_encrypted)


async def index_repository(db: AsyncSession, repository: Repository) -> None:
    """Download, chunk, embed, and store a repository's code. Updates
    `repository`'s indexing_status/file_count/chunk_count/indexed_at in
    place. Never raises -- failures are recorded on the repository itself
    (indexing_status="failed", indexing_error=<message>) so the job always
    completes and the failure is visible to the user, not just in logs."""
    repository.indexing_status = "indexing"
    repository.indexing_error = None
    await db.commit()

    try:
        access_token = await _get_access_token(db, repository.owner_id)

        client = GitHubClient(access_token)
        try:
            tarball = await client.download_tarball(repository.full_name)
        finally:
            await client.aclose()

        files = _extract_text_files(tarball)

        chunk_rows: list[CodeChunk] = []
        chunk_texts: list[str] = []
        for file_path, content in files.items():
            language = detect_language(file_path)
            for chunk in chunk_file(file_path, content):
                chunk_rows.append(
                    CodeChunk(
                        repository_id=repository.id,
                        file_path=file_path,
                        language=language,
                        chunk_type=chunk.chunk_type,
                        symbol_name=chunk.symbol_name,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        content=chunk.content,
                    )
                )
                chunk_texts.append(chunk.content)

        embeddings = await get_embedding_provider().embed(chunk_texts)
        for row, embedding in zip(chunk_rows, embeddings, strict=True):
            row.embedding = embedding

        await db.execute(delete(CodeChunk).where(CodeChunk.repository_id == repository.id))
        db.add_all(chunk_rows)

        repository.indexing_status = "indexed"
        repository.file_count = len(files)
        repository.chunk_count = len(chunk_rows)
        repository.indexed_at = datetime.now(UTC)
        await db.commit()

    except Exception as exc:
        logger.exception("Indexing failed for repository %s", repository.id)
        await db.rollback()
        repository.indexing_status = "failed"
        repository.indexing_error = str(exc)[:500]
        await db.commit()
