import pytest

from app.core.redis import redis_client


@pytest.fixture(autouse=True)
def _reset_redis_connection_pool() -> None:
    """redis_client is a single module-level singleton (see
    app/core/redis.py) -- correct for production (one process, one event
    loop), but fragile across this test suite, where different test files
    run on different event loops: FastAPI's TestClient owns its own portal
    loop (e.g. test_auth.py), while plain async tests get pytest-asyncio's
    fresh per-test loop by default. A pooled connection opened on one loop
    can't be reused once that loop closes ("RuntimeError: ... got Future
    attached to a different loop").

    `.disconnect()` (the "proper" async close) doesn't work as the fix here
    -- it tries to gracefully close each connection's transport over an
    *awaited* handshake, which itself needs the same loop that opened it,
    and raises the very same cross-loop error if that loop is already gone.
    `.reset()` is a plain sync call that just drops the pool's internal
    connection references without trying to close them -- the abandoned
    sockets belong to an already-dead loop regardless, and the next Redis
    command transparently opens a fresh connection on whichever loop is
    running now. Sync (not async) deliberately: this needs to run before
    pytest-asyncio picks a loop for the test at all.
    """
    redis_client.connection_pool.reset()
