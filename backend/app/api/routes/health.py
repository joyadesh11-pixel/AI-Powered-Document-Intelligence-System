"""/health: database, Redis and queue depth. 503 when a dependency is down."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_redis, get_session

log = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

_QUEUE_KEY = "celery"   # Celery's default queue name


@router.get("/health")
def health(session: Session = Depends(get_session), redis: Any = Depends(get_redis)) -> JSONResponse:
    try:
        session.execute(text("SELECT 1"))
        db = "ok"
    except Exception as exc:
        log.warning("Health: database check failed: %s", type(exc).__name__)
        db = "down"

    queue_depth: int | None = None
    try:
        redis.ping()
        queue_depth = int(redis.llen(_QUEUE_KEY))
        redis_state = "ok"
    except Exception as exc:
        log.warning("Health: redis check failed: %s", type(exc).__name__)
        redis_state = "down"

    healthy = db == "ok" and redis_state == "ok"
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "db": db, "redis": redis_state,
         "queue_depth": queue_depth},
        status_code=200 if healthy else 503,
    )
