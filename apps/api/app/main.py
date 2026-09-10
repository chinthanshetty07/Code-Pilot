from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.issues import router as issues_router
from app.api.repositories import router as repositories_router
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler, http_exception_handler
from app.core.middleware import RequestIDMiddleware

settings = get_settings()


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

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(repositories_router)
    app.include_router(issues_router)

    return app


app = create_app()
