"""Unit-level scenario smoke tests (non-LLM paths only).

Full 15-scenario suite lives in tests/integration/test_scenarios.py.
These three tests confirm corrupt/unsupported/exception paths without needing LLM.
"""
from __future__ import annotations

import pytest

from app.pipeline.graph import process, _default_deps
from app.schemas.models import DocStatus


def test_corrupt_file_fails(fx):
    result = process(str(fx["corrupt"]), deps=_default_deps())
    assert result.status == DocStatus.failed


def test_unsupported_extension_fails(fx):
    result = process(str(fx["unsupported"]), deps=_default_deps())
    assert result.status == DocStatus.failed
