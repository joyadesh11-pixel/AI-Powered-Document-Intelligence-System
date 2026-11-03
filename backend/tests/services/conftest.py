"""Fixtures for DB-backed service tests.

Tests in this directory require a running Postgres instance.  If Postgres is
not reachable, all tests skip cleanly with a clear message.

Use DATABASE_URL_TEST env var to override the test database URL
(default: postgresql+psycopg2://dociq:dociq@localhost:5432/dociq_test).
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.base import create_all, Base
from app.services.embedder import FakeEmbedder

_DEFAULT_TEST_DB = "postgresql+psycopg2://dociq:dociq@localhost:5432/dociq_test"
_TEST_DB_URL = os.environ.get("DATABASE_URL_TEST", _DEFAULT_TEST_DB)

_TABLES_TO_TRUNCATE = [
    "corrections",
    "processing_events",
    "chunks",
    "validation_issues",
    "line_items",
    "invoices",
    "document_pages",
    "sheet_mapping_cache",
    "documents",
]


@pytest.fixture(scope="session")
def db_engine():
    try:
        engine = create_engine(_TEST_DB_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Postgres not reachable ({exc}) — DB tests skipped")
    create_all(engine)
    yield engine
    # Do NOT drop_all (the test DB may be shared / reused)
    engine.dispose()


@pytest.fixture(autouse=True)
def truncate_tables(db_engine):
    """Truncate all tables before each test so they start clean."""
    with db_engine.connect() as conn:
        for table in _TABLES_TO_TRUNCATE:
            conn.execute(text(f"TRUNCATE {table} CASCADE"))
        conn.commit()
    yield


@pytest.fixture
def session(db_engine):
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    with Session() as s:
        yield s
        s.rollback()


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()
