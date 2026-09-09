from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.db import check_database_connection
from app.core.redis import check_redis_connection

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness() -> JSONResponse:
    db_ok = await check_database_connection()
    redis_ok = await check_redis_connection()
    ready = db_ok and redis_ok

    body = {
        "status": "ok" if ready else "error",
        "checks": {
            "database": "ok" if db_ok else "error",
            "redis": "ok" if redis_ok else "error",
        },
    }
    return JSONResponse(content=body, status_code=200 if ready else 503)
