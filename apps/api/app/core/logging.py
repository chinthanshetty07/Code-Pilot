"""Structured (JSON) logging with request/job correlation (Milestone 11).

Every log line becomes one JSON object (timestamp, level, logger name,
message, exception info if any) instead of the default free-text format --
machine-parseable, grep/jq-able, ingestible by any log aggregator without
a custom parser. Hand-rolled rather than a dependency (python-json-logger,
structlog): the actual requirement is small and stdlib `logging` + `json`
covers it completely.

Correlation works the same way for both the API and the worker, via one
ContextVar rather than threading an id through every function call by
hand: app.core.middleware.RequestIDMiddleware sets it for the duration of
one HTTP request, and app.workers.worker's on_job_start/on_job_end hooks
set it for the duration of one background job. Either way, every log line
emitted anywhere during that request/job automatically carries the same
id -- e.g. to find everything that happened while processing one arq job,
filter logs for its request_id.
"""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class _RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()  # type: ignore[attr-defined]
        return True


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    """Called once at process startup -- both app.main's create_app() and
    app.workers.worker's on_startup hook use this same setup, so the API
    and the worker emit an identical format and both get request/job
    correlation for free.

    Also neutralizes uvicorn's own logging config, which otherwise keeps
    emitting its default plain-text lines (confirmed empirically -- the
    API's access log kept the "INFO:     1.2.3.4 - "GET /health..."
    format even after this ran, until this was added): uvicorn's
    "uvicorn"/"uvicorn.access" loggers ship with propagate=False and their
    own directly-attached handlers (see uvicorn.config.LOGGING_CONFIG), so
    reconfiguring the root logger alone never reaches them -- their
    records never traveled up to it in the first place. Stripping their
    handlers and flipping propagate back on routes them through the same
    root handler as everything else, so the API container's stdout is
    entirely JSON, not a mix of two formats.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(_JSONFormatter())
    handler.addFilter(_RequestIDFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
