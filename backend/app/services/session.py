"""Process-wide SQLAlchemy session factory for the API and workers.

Built lazily from DATABASE_URL on first use. Tests swap it with
set_session_factory() so the API and eager Celery tasks hit the test DB.
"""
from __future__ import annotations

import threading

from sqlalchemy.orm import Session, sessionmaker

from app.db.base import make_engine, make_session_factory
from app.pipeline.config import get_config

_lock = threading.Lock()
_factory: sessionmaker | None = None


def get_session_factory() -> sessionmaker:
    global _factory
    if _factory is None:
        with _lock:
            if _factory is None:
                _factory = make_session_factory(make_engine(get_config().DATABASE_URL))
    return _factory


def set_session_factory(factory: sessionmaker | None) -> sessionmaker | None:
    """Replace the factory; returns the previous one so tests can restore it."""
    global _factory
    with _lock:
        previous, _factory = _factory, factory
    return previous


def new_session() -> Session:
    return get_session_factory()()
