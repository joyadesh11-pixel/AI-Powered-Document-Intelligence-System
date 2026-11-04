"""FastAPI dependencies: DB session, embedder, task dispatcher, Redis, API-key auth."""
from __future__ import annotations

import hmac
import logging
import uuid
from collections.abc import Iterator
from typing import Any, Protocol

from fastapi import Header
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.pipeline.config import get_config
from app.services import providers
from app.services.session import new_session

log = logging.getLogger(__name__)


def get_session() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
    finally:
        session.close()


def get_embedder() -> Any:
    """The query embedder, or None when it cannot be built (search then degrades)."""
    try:
        return providers.get_embedder()
    except Exception as exc:
        log.warning("Embedder unavailable: %s", type(exc).__name__)
        return None


def get_redis() -> Any:
    import redis

    return redis.Redis.from_url(
        get_config().REDIS_URL, socket_connect_timeout=1, socket_timeout=1
    )


class Dispatcher(Protocol):
    def process(self, document_id: uuid.UUID | str) -> None: ...


class CeleryDispatcher:
    """Enqueues processing on the Celery worker (app.workers.tasks, checkpoint C)."""

    def process(self, document_id: uuid.UUID | str) -> None:
        from app.workers.tasks import process_document

        process_document.delay(str(document_id))


def get_dispatcher() -> Dispatcher:
    return CeleryDispatcher()


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """No-op when API_KEY is unset; otherwise a constant-time header comparison."""
    expected = get_config().API_KEY
    if not expected:
        return
    supplied = (x_api_key or "").encode()
    if not hmac.compare_digest(supplied, expected.encode()):
        raise ApiError(401, "UNAUTHORIZED", "Missing or invalid API key")
