import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.logging import request_id_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        # Also into the logging ContextVar (app.core.logging), not just
        # request.state -- request.state is only reachable from code that
        # actually has this Request object in hand, which most of a
        # request's own call stack (services, agents, the DB layer) never
        # does. The ContextVar makes every log line emitted anywhere
        # during this request carry the same id with no plumbing.
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response
