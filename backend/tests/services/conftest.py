"""DB-backed service tests: shared fixtures live in tests/conftest.py.

Every test here starts from empty tables and a temporary upload dir; all of
them skip cleanly when Postgres is not reachable.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_db(truncate_tables, upload_dir):
    yield
