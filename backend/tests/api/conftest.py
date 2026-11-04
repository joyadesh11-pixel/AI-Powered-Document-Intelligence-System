"""API fixtures: TestClient over the real app, real Postgres, inline task dispatcher.

Celery is not involved: the dispatcher dependency is replaced by one that runs
the same service calls a worker would, synchronously, with FakeLLM/FakeEmbedder.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_dispatcher, get_redis
from app.api.main import create_app
from app.services import indexing, processing
from app.services.session import new_session


class InlineDispatcher:
    def __init__(self, providers: dict) -> None:
        self.providers = providers
        self.enqueued: list[str] = []

    def process(self, document_id) -> None:
        self.enqueued.append(str(document_id))
        doc_uuid = uuid.UUID(str(document_id))
        with new_session() as s:
            outcome = processing.process_document(doc_uuid, s, llm=self.providers["llm"])
            if outcome.should_index:
                indexing.index_document(doc_uuid, s, self.providers["embedder"])
                s.commit()


class FakeRedis:
    def __init__(self, up: bool = True, depth: int = 0) -> None:
        self.up, self.depth = up, depth

    def ping(self) -> bool:
        if not self.up:
            raise ConnectionError("redis down")
        return True

    def llen(self, key: str) -> int:
        return self.depth


@pytest.fixture(autouse=True)
def _clean(truncate_tables, upload_dir):
    yield


@pytest.fixture
def redis_state() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def dispatcher(fake_providers) -> InlineDispatcher:
    return InlineDispatcher(fake_providers)


@pytest.fixture
def client(session_factory, fake_providers, dispatcher, redis_state):
    app = create_app()
    app.dependency_overrides[get_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_redis] = lambda: redis_state
    with TestClient(app) as c:
        yield c
