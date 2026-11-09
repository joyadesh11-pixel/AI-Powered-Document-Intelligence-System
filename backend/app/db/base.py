"""SQLAlchemy engine, session factory, and create_all helper."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def _json_default(obj):
    """Make dates and Decimals storable in JSONB columns (as ISO strings / exact strings)."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def json_serializer(value) -> str:
    return json.dumps(value, default=_json_default)


def make_engine(url: str):
    return create_engine(url, pool_pre_ping=True, json_serializer=json_serializer)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_all(engine) -> None:
    """Enable pgvector extension and create all tables (idempotent)."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(engine)
