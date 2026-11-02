"""Tests for pdf_text node (M3).

T1  digital_A_001.pdf    -> GT invoice_number + total in page_text[0];
                            data rows in tables[0] >= GT line_items count
T2  borderless_table.pdf -> find_tables() == [] first; pdfplumber recovers >=80% of GT amounts
T3  mixed.pdf            -> page 5 NOT in page_text; pages 0-4 ARE in page_text
T4  scanned.pdf          -> force digital_pages=[0]; page 0 NOT in page_text (empty text),
                            page 0 IN scanned_pages
T5  two_column.pdf       -> "LEFT_COL" index < "RIGHT_COL" index in page_text[0]
"""
from __future__ import annotations

import pytest
import pymupdf

from app.pipeline.nodes.pdf_text import pdf_text
from scripts.generate_fixtures import _fmt_amount_us


# --------------------------------------------------------------------------- helpers

def _text(path: str, digital_pages=None, scanned_pages=None) -> dict:
    with pymupdf.open(path) as doc:
        n = doc.page_count
    if digital_pages is None:
        digital_pages = list(range(n))
    if scanned_pages is None:
        scanned_pages = [p for p in range(n) if p not in set(digital_pages)]
    return pdf_text({
        "file_path": path,
        "page_count": n,
        "digital_pages": digital_pages,
        "scanned_pages": scanned_pages,
        "sim": {},
    })


# --------------------------------------------------------------------------- T1

def test_t1_digital_text_and_tables(fixture_dir, gt):
    entry = gt["digital_A_001.pdf"]
    inv = entry["invoices"][0]
    path = str(fixture_dir / "digital_A_001.pdf")

    result = _text(path)
    text0 = result.get("page_text", {}).get(0, "")

    assert inv["invoice_number"] in text0, (
        f"invoice_number {inv['invoice_number']!r} not found in page_text[0]"
    )
    # Total in printed form should appear in the PDF text
    printed_total = inv["printed"]["total"]
    assert printed_total in text0, (
        f"printed total {printed_total!r} not found in page_text[0]"
    )

    tables0 = result.get("tables", {}).get(0, [])
    assert tables0, "expected at least one table in tables[0] for template A"
    md = "\n".join(tables0)
    data_rows = [l for l in md.splitlines() if l.startswith("|") and "---" not in l]
    # Exclude header row
    data_rows = data_rows[1:]
    assert len(data_rows) >= len(inv["line_items"]), (
        f"expected >= {len(inv['line_items'])} data rows, got {len(data_rows)}"
    )


# --------------------------------------------------------------------------- T2

def test_t2_borderless_table_pdfplumber(fixture_dir, gt):
    path = str(fixture_dir / "borderless_table.pdf")
    entry = gt["borderless_table.pdf"]
    inv = entry["invoices"][0]

    # First: assert PyMuPDF find_tables finds nothing (no vector strokes)
    with pymupdf.open(path) as doc:
        found = doc[0].find_tables().tables
    assert found == [], (
        f"borderless_table.pdf should have no PyMuPDF-detected tables, "
        f"but found {len(found)}"
    )

    # Then: call pdf_text — pdfplumber fallback should recover the table
    result = _text(path)
    tables0 = result.get("tables", {}).get(0, [])
    assert tables0, (
        "expected pdfplumber to recover at least one table from borderless_table.pdf"
    )
    md = "\n".join(tables0)

    # Count how many GT line_items amounts appear in the markdown
    gt_amts = [_fmt_amount_us(f"{float(it['amount']):.2f}") for it in inv["line_items"]]
    found_count = sum(1 for a in gt_amts if a in md)
    pct = found_count / len(gt_amts)
    if pct < 0.80:
        pytest.fail(
            f"pdfplumber recovered only {found_count}/{len(gt_amts)} GT amounts "
            f"({pct:.0%}); need >=80%. Missing: "
            f"{[a for a in gt_amts if a not in md]}"
        )


# --------------------------------------------------------------------------- T3

def test_t3_mixed_digital_scanned_split(fixture_dir):
    path = str(fixture_dir / "mixed.pdf")
    result = _text(path, digital_pages=list(range(5)), scanned_pages=[5])

    page_text = result.get("page_text", {})
    for p in range(5):
        assert p in page_text, f"page {p} should be in page_text (digital)"
    assert 5 not in page_text, "page 5 (scanned) should NOT be in page_text"


# --------------------------------------------------------------------------- T4

def test_t4_empty_text_layer_moves_to_scanned(fixture_dir):
    """scanned.pdf forced into digital_pages=[0]; empty text -> moved to scanned_pages."""
    path = str(fixture_dir / "scanned.pdf")
    result = _text(path, digital_pages=[0], scanned_pages=[1, 2])

    assert 0 not in result.get("page_text", {}), (
        "page 0 has no text layer; should have been moved out of page_text"
    )
    assert 0 in result.get("scanned_pages", []), (
        "page 0 should appear in scanned_pages after empty-text demotion"
    )


# --------------------------------------------------------------------------- T5

def test_t5_two_column_order(fixture_dir):
    path = str(fixture_dir / "two_column.pdf")
    result = _text(path)
    text0 = result.get("page_text", {}).get(0, "")
    assert "LEFT_COL" in text0, "LEFT_COL marker not found in page_text[0]"
    assert "RIGHT_COL" in text0, "RIGHT_COL marker not found in page_text[0]"
    assert text0.index("LEFT_COL") < text0.index("RIGHT_COL"), (
        "LEFT_COL should appear before RIGHT_COL in sorted text"
    )
