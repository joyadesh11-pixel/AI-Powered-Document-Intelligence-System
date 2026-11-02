"""Intake node tests T1-T10 (docs/nodes/intake.md).

Calls make_intake(deps)(state) directly — no full pipeline run.
Tests build fixtures via the session-scoped fixture_dir from conftest.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.config import Config
from app.pipeline.nodes.intake import make_intake
from app.pipeline.state import Deps
from app.schemas.models import ErrorCode, IssueCode


def _run(path: Path, deps: Deps | None = None) -> dict:
    """Run the intake node on *path* and return the raw state-update dict."""
    if deps is None:
        deps = Deps(config=Config(), llm=None, seen_store=None, mapping_cache=None)
    node_fn = make_intake(deps)
    state = {"file_path": str(path), "history": []}
    result = node_fn(state)
    return result


# ------------------------------------------------------------------ T1: valid files

@pytest.mark.parametrize("fname,expected_type,expected_pages", [
    ("digital_A_001.pdf", "pdf", None),   # page count >= 1
    ("valid_001.png",     "image", 1),
    ("valid_001.jpg",     "image", 1),
    ("multiframe_001.tiff", "image", 3),
    ("tabular_001.xlsx",  "sheet", 0),
    ("legacy_001.xls",   "sheet", 0),
    ("utf8_001.csv",     "sheet", 0),
])
def test_t1_valid_files(fname, expected_type, expected_pages, fixture_dir):
    result = _run(fixture_dir / fname)
    assert "error_code" not in result or result.get("error_code") is None, (
        f"{fname}: unexpected error {result.get('error_code')}: {result.get('error')}"
    )
    assert result["file_type"] == expected_type, f"{fname}: wrong file_type"
    if expected_pages is not None:
        assert result["page_count"] == expected_pages, f"{fname}: wrong page_count"
    else:
        assert result["page_count"] >= 1, f"{fname}: expected >= 1 pages"
    assert result["file_hash"]  # non-empty string
    assert result["size_bytes"] > 0


# ------------------------------------------------------------------ T2: PDF renamed .jpg

def test_t2_pdf_renamed_jpg(fixture_dir):
    result = _run(fixture_dir / "edge_pdf_as_jpg.jpg")
    assert result["file_type"] == "pdf", "magic bytes should override extension"
    warning_codes = [w["code"] for w in result.get("warnings", [])]
    assert IssueCode.EXT_MISMATCH.value in warning_codes, (
        f"expected EXT_MISMATCH warning; got {warning_codes}"
    )


# ------------------------------------------------------------------ T3: truncated PDF

def test_t3_truncated_pdf(fixture_dir):
    result = _run(fixture_dir / "bad_truncated.pdf")
    assert result.get("error_code") == ErrorCode.CORRUPT_FILE.value, (
        f"expected CORRUPT_FILE; got {result.get('error_code')}: {result.get('error')}"
    )
    assert result.get("status") == "failed"


# ------------------------------------------------------------------ T4: encrypted PDF

def test_t4_encrypted_pdf(fixture_dir):
    result = _run(fixture_dir / "bad_encrypted.pdf")
    assert result.get("error_code") == ErrorCode.ENCRYPTED.value, (
        f"expected ENCRYPTED; got {result.get('error_code')}: {result.get('error')}"
    )
    assert result.get("status") == "failed"


# ------------------------------------------------------------------ T5: 0-byte file

def test_t5_empty_file(fixture_dir):
    result = _run(fixture_dir / "bad_empty.bin")
    assert result.get("error_code") == ErrorCode.EMPTY_FILE.value
    assert result.get("status") == "failed"


# ------------------------------------------------------------------ T6: .docx -> UNSUPPORTED_TYPE

def test_t6_docx_unsupported(fixture_dir):
    result = _run(fixture_dir / "bad_docx.docx")
    assert result.get("error_code") == ErrorCode.UNSUPPORTED_TYPE.value, (
        f"expected UNSUPPORTED_TYPE; got {result.get('error_code')}: {result.get('error')}"
    )
    assert result.get("status") == "failed"


# ------------------------------------------------------------------ T7: MAX_FILE_MB=0

def test_t7_too_large(fixture_dir):
    deps = Deps(config=Config(MAX_FILE_MB=0), llm=None, seen_store=None, mapping_cache=None)
    result = _run(fixture_dir / "digital_A_001.pdf", deps=deps)
    assert result.get("error_code") == ErrorCode.TOO_LARGE.value
    assert result.get("status") == "failed"


# ------------------------------------------------------------------ T8: zip-bomb

def test_t8_zipbomb(fixture_dir):
    # edge_zipbomb.xlsx streams 5 MB unzipped; we use MAX_UNZIPPED_MB=1 to trigger
    deps = Deps(
        config=Config(MAX_UNZIPPED_MB=1),
        llm=None, seen_store=None, mapping_cache=None,
    )
    result = _run(fixture_dir / "edge_zipbomb.xlsx", deps=deps)
    assert result.get("error_code") == ErrorCode.TOO_LARGE.value, (
        f"expected TOO_LARGE; got {result.get('error_code')}: {result.get('error')}"
    )
    assert result.get("status") == "failed"


# ------------------------------------------------------------------ T9: truncated PNG continues

def test_t9_truncated_png(fixture_dir):
    result = _run(fixture_dir / "edge_truncated.png")
    # Should continue (not failed) with TRUNCATED_IMAGE warning
    assert result.get("status") != "failed", (
        f"truncated PNG should not fail; got error {result.get('error_code')}"
    )
    assert result["file_type"] == "image"
    warning_codes = [w["code"] for w in result.get("warnings", [])]
    assert IssueCode.TRUNCATED_IMAGE.value in warning_codes, (
        f"expected TRUNCATED_IMAGE warning; got {warning_codes}"
    )


# ------------------------------------------------------------------ T10: deterministic hash

def test_t10_same_bytes_same_hash(fixture_dir):
    r1 = _run(fixture_dir / "digital_A_001.pdf")
    r2 = _run(fixture_dir / "digital_A_001.pdf")
    assert r1["file_hash"] == r2["file_hash"], "same file should produce same hash"
    assert r1["file_hash"] != "stub_hash", "hash must not be stub"
