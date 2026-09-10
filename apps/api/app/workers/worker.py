from arq.connections import RedisSettings
from arq.worker import func

from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.workers.tasks import (
    create_code_change_task,
    create_plan_task,
    create_pull_request_task,
    create_review_task,
    create_test_run_task,
    index_repository_task,
)

settings = get_settings()


async def ping(ctx: dict) -> str:
    return "pong"


async def on_startup(ctx: dict) -> None:
    configure_logging()


async def on_job_start(ctx: dict) -> None:
    # Same ContextVar RequestIDMiddleware sets per HTTP request (see
    # app/core/logging.py and app/core/middleware.py) -- here set once per
    # background job instead, so every log line anywhere in a job's work
    # (including inside an agent's own loop) carries the same id. No
    # explicit reset on job end: arq runs each job as its own dedicated
    # asyncio Task (confirmed against the installed arq version's actual
    # source -- `self.loop.create_task(self.run_job(...))` per job), so
    # this ContextVar set is already scoped to just that task and is
    # discarded with it once the job finishes -- nothing to leak into the
    # next one.
    request_id_var.set(f"job:{ctx['job_id']}")


class WorkerSettings:
    on_startup = on_startup
    on_job_start = on_job_start
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
        # No timeout override needed: the Reviewer's loop is read-only and
        # bounded at MAX_TURNS=8 (see app/agents/reviewer.py), nowhere near
        # long enough to need more than the default job_timeout below.
        create_review_task,
        # No LLM loop at all (see app/services/pull_requests.py) -- a fixed
        # sequence of git/API calls, well within the default job_timeout.
        create_pull_request_task,
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 600
