"""Dry-run scenario tests.

Covers all 13 scenarios (original 12 + scenario 13 from Fix 1).
Intake now uses real file I/O; downstream nodes still use sim switches (removed in M7).

Each scenario maps to a fixture file via the session-scoped `fx` dict in conftest.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.graph import process, _default_deps
from app.schemas.models import DocStatus


# (label, file_key, sim, expected_status, assert_no_node)
# file_key is looked up in the fx fixture at test time.
SCENARIOS = [
    ("digital PDF, clean",
     "pdf", {}, DocStatus.validated_ok, None),

    ("scanned image",
     "image", {}, DocStatus.validated_ok, None),

    ("mixed PDF + low-conf OCR unit",
     "mixed", {"units": 2, "low_conf_units": [1]},
     DocStatus.validated_ok, None),

    ("arith error fixed by vision",
     "pdf", {"arith_fail_units": [0]}, DocStatus.validated_ok, None),

    ("arith error persists",
     "pdf", {"persistent_fail_units": [0]}, DocStatus.needs_review, None),

    ("xlsx tabular",
     "xlsx", {}, DocStatus.validated_ok, None),

    ("xlsx mapping bad once (retry)",
     "xlsx", {"bad_mapping": 1}, DocStatus.validated_ok, None),

    ("xlsx mapping bad twice",
     "xlsx", {"bad_mapping": 2}, DocStatus.needs_review, None),

    ("csv free-form",
     "freeform_csv", {"freeform": True}, DocStatus.validated_ok, None),

    ("corrupt file",
     "corrupt", {}, DocStatus.failed, None),

    ("unsupported .docx",
     "unsupported", {}, DocStatus.failed, None),

    ("exception inside segment",
     "pdf", {"raise_in": "segment"}, DocStatus.failed, None),

    # Scenario 13 — Fix 1: xlsx with arithmetic error must NOT escalate to vision
    ("xlsx arithmetic error -> needs_review, no vision",
     "xlsx", {"arith_fail_units": [0]}, DocStatus.needs_review, "vision_extract"),
]


@pytest.mark.parametrize(
    "label,file_key,sim,expected_status,assert_no_node",
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_scenario(label, file_key, sim, expected_status, assert_no_node, fx, gt):
    file_path = str(fx[file_key])
    filename = Path(file_path).name
    full_sim = {**sim, "gt_invoices": {
        filename: (gt.get(filename) or {}).get("invoices", [])
    }}
    result = process(file_path, deps=_default_deps(), _sim=full_sim)
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
