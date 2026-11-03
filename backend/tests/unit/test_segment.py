"""Tests for the segment node (T1-T8 from spec)."""
from __future__ import annotations

import pytest

from app.pipeline.nodes.segment import segment
from app.schemas.models import IssueCode


def _run(page_text: dict, *, file_type: str = "pdf", page_count: int | None = None,
         page_source: dict | None = None, low_conf_pages: list | None = None,
         tables: dict | None = None) -> dict:
    state = {
        "file_type": file_type,
        "page_text": page_text,
        "page_count": page_count or (max(page_text.keys()) + 1 if page_text else 0),
        "page_source": page_source or {p: "digital" for p in page_text},
        "low_conf_pages": low_conf_pages or [],
        "tables": tables or {},
        "warnings": [],
    }
    return segment(state)


# ------------------------------------------------------------------ T1

def test_single_page_with_anchor_one_unit():
    """T1: single page with invoice anchor → 1 unit covering page 0."""
    result = _run({0: "Invoice No. INV-001\nAmount: 100.00\nTotal Due: 100.00"})
    units = result["units"]
    assert len(units) == 1
    assert units[0]["pages"] == [0]
    assert units[0]["id"] == 0


# ------------------------------------------------------------------ T2

def test_three_pages_pxy_continuation_one_unit():
    """T2: 3 pages with 'Page N of 3' markers, same anchor → 1 unit."""
    text = {
        0: "Invoice No. INV-001\nPage 1 of 3",
        1: "Continued\nPage 2 of 3",
        2: "Total: $300\nPage 3 of 3",
    }
    result = _run(text)
    units = result["units"]
    assert len(units) == 1
    assert units[0]["pages"] == [0, 1, 2]


# ------------------------------------------------------------------ T3

def test_alternating_anchors_split_into_units():
    """T3: pages alternate between two distinct invoice anchors → splits correctly."""
    text = {}
    for i in range(10):
        anchor = "INV-A" if i % 2 == 0 else "INV-B"
        text[i] = f"Invoice No. {anchor}\nTotal Due: 100.00"
    result = _run(text)
    units = result["units"]
    # Each page has a different anchor from the previous → 10 units
    # (every page starts a new unit because its anchor differs from prev)
    assert len(units) == 10
    for uid, u in enumerate(units):
        assert u["id"] == uid
        assert len(u["pages"]) == 1


# ------------------------------------------------------------------ T4

def test_no_anchor_fallback_one_unit_per_page():
    """T4: no anchor on any page → 1 unit per page + SEGMENT_FALLBACK warning."""
    text = {
        0: "Acme Corp\nSome text here",
        1: "More items and amounts",
        2: "Final page",
    }
    result = _run(text)
    units = result["units"]
    assert len(units) == 3
    for i, u in enumerate(units):
        assert u["pages"] == [i]
    codes = [w.get("code") or w.get("code") for w in result.get("warnings", [])]
    assert IssueCode.SEGMENT_FALLBACK.value in codes


# ------------------------------------------------------------------ T5

def test_single_page_with_multiple_anchor_patterns_one_unit():
    """T5: 1 page, two patterns both matching → 1 unit (no crash, documented limitation)."""
    result = _run({0: "Invoice No. INV-001 Bill No. BILL-002 Total Due: 100.00"})
    units = result["units"]
    assert len(units) == 1
    assert units[0]["pages"] == [0]


# ------------------------------------------------------------------ T6

def test_needs_vision_set_for_low_conf_page():
    """T6: unit spans pages [0,1]; page 1 is low-conf → needs_vision=True."""
    text = {
        0: "Invoice No. INV-001\nPage 1 of 2",
        1: "continued content\nPage 2 of 2",
    }
    result = _run(text, low_conf_pages=[1])
    units = result["units"]
    assert len(units) == 1
    assert units[0]["needs_vision"] is True
    assert 1 in units[0]["low_conf_pages"]


# ------------------------------------------------------------------ T7

def test_sheet_file_type_one_unit_per_sheet():
    """T7: file_type=sheet → one unit per sheet/page."""
    text = {
        0: "Invoice Number: SH-001\nTotal: 100.00",
        1: "Invoice Number: SH-002\nTotal: 200.00",
    }
    result = _run(
        text,
        file_type="sheet",
        page_source={0: "sheet", 1: "sheet"},
    )
    units = result["units"]
    assert len(units) == 2
    assert units[0]["source"] == "sheet"
    assert units[1]["source"] == "sheet"
    assert units[0]["pages"] == [0]
    assert units[1]["pages"] == [1]


# ------------------------------------------------------------------ T8

def test_invariant_all_pages_covered_no_overlap():
    """T8: union of all unit page lists == all pages; sets are disjoint."""
    text = {
        0: "Invoice No. INV-001\nTotal Due: 100",
        1: "Continued\nPage 2 of 3",
        2: "Final\nPage 3 of 3",
        3: "Invoice No. INV-002\nTotal Due: 200",
    }
    result = _run(text)
    units = result["units"]
    all_covered: list[int] = []
    for u in units:
        all_covered.extend(u["pages"])
    # All pages covered
    assert set(all_covered) == set(text.keys())
    # No overlap
    assert len(all_covered) == len(set(all_covered))


# ------------------------------------------------------------------ Extra: image file type

def test_image_file_type_one_unit_per_page():
    """Image file → one unit per page regardless of text content."""
    text = {
        0: "Invoice No. INV-001\nTotal: 100",
        1: "Invoice No. INV-002\nTotal: 200",
    }
    result = _run(
        text,
        file_type="image",
        page_source={0: "ocr", 1: "ocr"},
    )
    units = result["units"]
    assert len(units) == 2
    assert units[0]["pages"] == [0]
    assert units[1]["pages"] == [1]


def test_segment_fallback_warning_has_correct_code():
    """SEGMENT_FALLBACK warning has info severity and expected code."""
    result = _run({0: "no anchor text here", 1: "also nothing"})
    codes = [w.get("code") for w in result.get("warnings", [])]
    assert IssueCode.SEGMENT_FALLBACK.value in codes
    fallback_w = next(
        w for w in result["warnings"]
        if w.get("code") == IssueCode.SEGMENT_FALLBACK.value
    )
    assert fallback_w["severity"] == "info"
