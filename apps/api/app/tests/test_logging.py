import io
import json
import logging

from app.core.logging import _JSONFormatter, _RequestIDFilter, configure_logging, request_id_var


def _make_record(message: str = "hello", level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_json_formatter_produces_valid_json_with_expected_fields() -> None:
    record = _make_record("something happened")
    formatted = _JSONFormatter().format(record)

    payload = json.loads(formatted)
    assert payload["message"] == "something happened"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "timestamp" in payload


def test_json_formatter_includes_exception_info() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _make_record("failed", level=logging.ERROR)
        record.exc_info = sys.exc_info()

    payload = json.loads(_JSONFormatter().format(record))
    assert "boom" in payload["exception"]
    assert "ValueError" in payload["exception"]


def test_request_id_filter_injects_the_current_contextvar_value() -> None:
    token = request_id_var.set("test-request-id-123")
    try:
        record = _make_record()
        _RequestIDFilter().filter(record)
        assert record.request_id == "test-request-id-123"  # type: ignore[attr-defined]
    finally:
        request_id_var.reset(token)


def test_request_id_filter_is_none_when_nothing_is_set() -> None:
    # No request/job in flight -- must not raise, just report None rather
    # than a stale value from an earlier test (contextvars are isolated
    # per-task, but this guards the filter's own default explicitly).
    record = _make_record()
    _RequestIDFilter().filter(record)
    assert record.request_id is None  # type: ignore[attr-defined]


def test_end_to_end_log_line_carries_the_request_id() -> None:
    """A log line emitted anywhere while request_id_var is set must carry
    it in the final JSON output -- the actual mechanism
    RequestIDMiddleware and the worker's on_job_start hook rely on."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JSONFormatter())
    handler.addFilter(_RequestIDFilter())

    logger = logging.getLogger("app.test.end_to_end")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    token = request_id_var.set("job:abc123")
    try:
        logger.info("doing the thing")
    finally:
        request_id_var.reset(token)
        logger.removeHandler(handler)

    payload = json.loads(stream.getvalue().strip())
    assert payload["request_id"] == "job:abc123"
    assert payload["message"] == "doing the thing"


def test_configure_logging_routes_uvicorns_own_loggers_through_json_too() -> None:
    """Regression test for a real gap found while verifying this milestone
    live: uvicorn.access/uvicorn.error ship with propagate=False and their
    own directly-attached handlers, so reconfiguring only the root logger
    (as an earlier version of configure_logging did) left the API
    container's access log in uvicorn's own plain-text format instead of
    JSON -- confirmed by literally reading the container's stdout, not
    assumed. configure_logging must strip those loggers' own handlers and
    flip propagate back on so their records reach the same JSON handler as
    everything else."""
    # Give uvicorn's loggers a handler of their own first, the same shape
    # uvicorn.config.LOGGING_CONFIG does -- so this test actually exercises
    # configure_logging *removing* it, not just finding them already empty.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addHandler(logging.StreamHandler())
        logging.getLogger(name).propagate = False

    configure_logging()

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        assert logger.handlers == []
        assert logger.propagate is True
