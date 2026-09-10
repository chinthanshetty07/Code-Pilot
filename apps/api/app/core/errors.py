from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _error_body(code: str, message: str, request: Request) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
        }
    }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content=_error_body(exc.code, exc.message, request)
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(f"HTTP_{exc.status_code}", str(exc.detail), request),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # FastAPI/Pydantic raise this directly for a malformed path/query/body
    # value (e.g. a non-UUID repository_id) -- it isn't a StarletteHTTPException
    # subclass, so it bypasses http_exception_handler above and, unregistered,
    # would leak FastAPI's own default {"detail": [...]} shape instead of this
    # API's consistent {"error": {code, message, request_id}} envelope.
    message = "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
    )
    return JSONResponse(
        status_code=422,
        content=_error_body("HTTP_422", message, request),
    )
