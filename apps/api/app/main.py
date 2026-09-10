from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.issues import router as issues_router
from app.api.repositories import router as repositories_router
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    app_error_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware

settings = get_settings()
configure_logging()


def create_app() -> FastAPI:
    app = FastAPI(
        title="CodePilot API",
        description="AI-powered software engineering platform API.",
        version="0.1.0",
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Starlette's add_exception_handler wants a handler typed for the generic
    # Exception; these are correctly typed for the specific subclass they're
    # registered against, which Starlette dispatches to by exact/subclass
    # match at runtime -- standard FastAPI/Starlette typing friction.
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,  # type: ignore[arg-type]
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(repositories_router)
    app.include_router(issues_router)

    return app


app = create_app()
