"""Fail node tests T1-T2 (docs/nodes/fail.md).

Uses process() with a real corrupt fixture so intake routes to fail.
"""
from __future__ import annotations

import pytest

from app.pipeline.graph import process, _default_deps
from app.schemas.models import DocStatus, ErrorCode, PipelineResult


def test_t1_corrupt_fixture_status_and_history(fixture_dir):
    result = process(str(fixture_dir / "bad_truncated.pdf"), deps=_default_deps())
    assert result.status == DocStatus.failed
    assert result.error_code == ErrorCode.CORRUPT_FILE, (
        f"expected CORRUPT_FILE; got {result.error_code}"
    )
    assert result.invoices == []
    visited = [h["node"] for h in result.history]
    assert visited[-1] == "fail", f"history should end with 'fail'; got {visited}"


def test_t2_result_validates_against_schema(fixture_dir):
    result = process(str(fixture_dir / "bad_truncated.pdf"), deps=_default_deps())
    # Round-trip through model_dump / model_validate
    raw = result.model_dump()
    revalidated = PipelineResult.model_validate(raw)
    assert revalidated.status == result.status
    assert revalidated.error_code == result.error_code
