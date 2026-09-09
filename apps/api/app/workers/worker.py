from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers.tasks import index_repository_task

settings = get_settings()


async def ping(ctx: dict) -> str:
    return "pong"


class WorkerSettings:
    functions = [ping, index_repository_task]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 600
