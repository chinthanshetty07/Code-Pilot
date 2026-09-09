from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

# NullPool: opens a fresh DB connection per checkout instead of reusing a
# pooled one. Avoids "attached to a different loop" errors when the async
# engine is used across multiple event loops (e.g. TestClient's internal
# portal loop vs. pytest-asyncio's per-test loop). Fine for this app's scale;
# revisit if connection-per-request overhead ever matters under real load.
engine = create_async_engine(settings.database_url, pool_pre_ping=True, poolclass=NullPool)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def check_database_connection() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
