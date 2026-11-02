"""Dry-run scenario tests.

Covers all 12 original scenarios from reference/graph_skeleton/run_demo.py plus
the new scenario 13 (Fix 1): xlsx arithmetic error -> needs_review, no vision.

These are unit-level integration tests against the stub pipeline with sim switches.
No real LLM, OCR, or file I/O.
"""
from __future__ import annotations

import pytest

from app.pipeline.graph import process
from app.schemas.models import DocStatus


# (label, file_path, sim, expected_status, assert_no_node)
SCENARIOS = [
    ("digital PDF, clean",
     "a.pdf", {}, DocStatus.validated_ok, None),

    ("scanned image",
     "a.png", {}, DocStatus.validated_ok, None),

    ("mixed PDF + low-conf OCR unit",
     "a.pdf", {"scanned_pages": [2], "units": 2, "low_conf_units": [1]},
     DocStatus.validated_ok, None),

    ("arith error fixed by vision",
     "a.pdf", {"arith_fail_units": [0]}, DocStatus.validated_ok, None),

    ("arith error persists",
     "a.pdf", {"persistent_fail_units": [0]}, DocStatus.needs_review, None),

    ("xlsx tabular",
     "a.xlsx", {}, DocStatus.validated_ok, None),

    ("xlsx mapping bad once (retry)",
     "a.xlsx", {"bad_mapping": 1}, DocStatus.validated_ok, None),

    ("xlsx mapping bad twice",
     "a.xlsx", {"bad_mapping": 2}, DocStatus.needs_review, None),

    ("csv free-form",
     "a.csv", {"freeform": True}, DocStatus.validated_ok, None),

    ("corrupt file",
     "a.pdf", {"corrupt": True}, DocStatus.failed, None),

    ("unsupported .docx",
     "a.docx", {}, DocStatus.failed, None),

    ("exception inside segment",
     "a.pdf", {"raise_in": "segment"}, DocStatus.failed, None),

    # Scenario 13 — Fix 1: xlsx with arithmetic error must NOT escalate to vision
    ("xlsx arithmetic error -> needs_review, no vision",
     "a.xlsx", {"arith_fail_units": [0]}, DocStatus.needs_review, "vision_extract"),
]


@pytest.mark.parametrize(
    "label,file_path,sim,expected_status,assert_no_node",
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_scenario(label, file_path, sim, expected_status, assert_no_node):
    result = process(file_path, _sim=sim)
    assert result.status == expected_status, (
        f"[{label}] expected {expected_status!r}, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}"
    )
    if assert_no_node:
        visited = [h["node"] for h in result.history]
        assert assert_no_node not in visited, (
            f"[{label}] expected {assert_no_node!r} NOT to run, "
            f"but history was: {visited}"
        )
