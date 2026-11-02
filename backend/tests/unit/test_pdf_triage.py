"""Tests for pdf_triage node (M3).

T1  digital_A_001.pdf     -> all page_labels == "digital"
T2  scanned.pdf           -> all page_labels == "scanned"
T3  mixed.pdf             -> pages 0-4 digital, page 5 scanned
T4  garbled_cid.pdf       -> all page_labels == "garbled"
T5  blank_page.pdf        -> page 0 == "scanned" (rule 1: chars < PAGE_MIN_CHARS)
T6a scan_with_pagenum.pdf -> page 0 == "scanned" (rule 3: img_cover > IMG_COVER_MAX)
T6b scan_with_pagenum_short.pdf -> page 0 == "scanned" (rule 1)
T7  coverage              -> page_labels keys == set(range(page_count)); no overlap between lists
T8  garbled_pua.pdf       -> all "garbled" (skipped if file absent)
"""
from __future__ import annotations

import pytest
import pymupdf

from app.pipeline.nodes.pdf_triage import pdf_triage, _garbled_ratio


# --------------------------------------------------------------------------- helpers

def _triage(path: str) -> dict:
    with pymupdf.open(path) as doc:
        n = doc.page_count
    return pdf_triage({"file_path": path, "page_count": n, "sim": {}})


# --------------------------------------------------------------------------- unit test: _garbled_ratio

def test_garbled_ratio_cid_escapes():
    # (cid:N) fragments
    text_cid = " ".join(f"(cid:{i})" for i in range(1, 10))
    # Private-use area chars U+E000..U+F8FF (BMP only) — escape sequences
    text_pua = "" * 30 + " hello"
    text_clean = "Invoice total 123.45"

    assert _garbled_ratio(text_cid) > 0.20, "cid fragments should exceed threshold"
    assert _garbled_ratio(text_pua) > 0.20, "PUA chars should exceed threshold"
    assert _garbled_ratio(text_clean) < 0.20, "clean text should be below threshold"
    assert _garbled_ratio("") == 0.0


# --------------------------------------------------------------------------- T1-T7

def test_t1_all_digital(fixture_dir):
    result = _triage(str(fixture_dir / "digital_A_001.pdf"))
    assert result["page_labels"], "page_labels must not be empty"
    assert all(v == "digital" for v in result["page_labels"].values()), (
        f"expected all digital, got {result['page_labels']}"
    )


def test_t2_all_scanned(fixture_dir):
    result = _triage(str(fixture_dir / "scanned.pdf"))
    assert all(v == "scanned" for v in result["page_labels"].values()), (
        f"expected all scanned, got {result['page_labels']}"
    )


def test_t3_mixed(fixture_dir):
    result = _triage(str(fixture_dir / "mixed.pdf"))
    labels = result["page_labels"]
    for p in range(5):
        assert labels[p] == "digital", f"page {p} should be digital, got {labels[p]}"
    assert labels[5] == "scanned", f"page 5 should be scanned, got {labels[5]}"


def test_t4_garbled_cid(fixture_dir):
    result = _triage(str(fixture_dir / "garbled_cid.pdf"))
    assert all(v == "garbled" for v in result["page_labels"].values()), (
        f"expected all garbled, got {result['page_labels']}"
    )


def test_t5_blank_page(fixture_dir):
    result = _triage(str(fixture_dir / "blank_page.pdf"))
    assert result["page_labels"][0] == "scanned", (
        f"blank page should be scanned (rule 1), got {result['page_labels'][0]}"
    )


def test_t6a_scan_with_pagenum_rule3(fixture_dir):
    """scan_with_pagenum.pdf: image_cover > IMG_COVER_MAX, chars in [PAGE_MIN_CHARS, SCAN_TEXT_MAX_CHARS)."""
    result = _triage(str(fixture_dir / "scan_with_pagenum.pdf"))
    assert result["page_labels"][0] == "scanned", (
        f"page with full-image+stray-text should be scanned (rule 3), got {result['page_labels'][0]}"
    )


def test_t6b_scan_with_pagenum_rule1(fixture_dir):
    """scan_with_pagenum_short.pdf: 3 chars < PAGE_MIN_CHARS(50) -> rule 1."""
    result = _triage(str(fixture_dir / "scan_with_pagenum_short.pdf"))
    assert result["page_labels"][0] == "scanned", (
        f"page with 3-char text should be scanned (rule 1), got {result['page_labels'][0]}"
    )


def test_t7_coverage(fixture_dir):
    result = _triage(str(fixture_dir / "digital_A_001.pdf"))
    n = len(result["page_labels"])
    assert set(result["page_labels"].keys()) == set(range(n)), (
        "page_labels must cover every page index exactly once"
    )
    d_set = set(result["digital_pages"])
    s_set = set(result["scanned_pages"])
    assert d_set.isdisjoint(s_set), "no index should appear in both digital_pages and scanned_pages"
    assert d_set | s_set == set(result["page_labels"].keys()), (
        "digital_pages + scanned_pages must cover all page indices"
    )


def test_t8_garbled_pua(fixture_dir):
    pua_path = fixture_dir / "garbled_pua.pdf"
    if not pua_path.exists():
        pytest.skip("garbled_pua.pdf not generated (self-check failed at fixture time)")
    result = _triage(str(pua_path))
    assert all(v == "garbled" for v in result["page_labels"].values()), (
        f"expected all garbled for PUA font PDF, got {result['page_labels']}"
    )
