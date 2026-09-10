from arq.connections import RedisSettings
from arq.worker import func

from app.core.config import get_settings
from app.workers.tasks import (
    create_code_change_task,
    create_plan_task,
    create_test_run_task,
    index_repository_task,
)

settings = get_settings()


async def ping(ctx: dict) -> str:
    return "pong"


class WorkerSettings:
    functions = [
        ping,
        index_repository_task,
        create_plan_task,
        create_code_change_task,
        # Longer than the default job_timeout below: Milestone 8's fix loop
        # can run the sandbox up to MAX_FIX_ATTEMPTS+1 times, plus that many
        # Coder-agent fix passes in between, all inside this one job. func()
        # defaults the registered task name to the wrapped coroutine's
        # __qualname__ ("create_test_run_task"), so this doesn't change what
        # app/api/issues.py's enqueue_job(...) call needs to name -- confirmed
        # against the installed arq version's actual source, not assumed.
        func(create_test_run_task, timeout=1800),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 600
