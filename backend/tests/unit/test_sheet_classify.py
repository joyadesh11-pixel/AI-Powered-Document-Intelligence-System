"""Tests for sheet_classify node — M4.

T1  xlsx_simple_linegrain (200+ rows) → tabular
T2  xlsx_freeform_one_invoice → freeform; page_text[0] contains text
T3  csv_freeform → freeform
T4  xlsx_2sheets_tabular_plus_note → tabular + SHEET_SKIPPED warning
T5  perf_5000x30 → tabular
T6  xlsx_tiny_4row → tabular
"""
from __future__ import annotations

import pytest

from app.pipeline.nodes.sheet_load import sheet_load
from app.pipeline.nodes.sheet_classify import sheet_classify
from app.schemas.models import IssueCode


def _load_and_classify(path, file_type="sheet"):
    state = {"file_path": str(path), "file_type": file_type, "sim": {}, "warnings": []}
    load_result = sheet_load(state)
    state2 = {**state, **load_result}
    classify_result = sheet_classify(state2)
    return {**state2, **classify_result}


def test_T1_xlsx_simple_linegrain_tabular(fx):
    result = _load_and_classify(fx["xlsx_simple_linegrain"])
    assert result.get("sheet_kind") == "tabular"


def test_T2_xlsx_freeform_page_text(fx):
    result = _load_and_classify(fx["xlsx_freeform_one_invoice"])
    assert result.get("sheet_kind") == "freeform"
    page_text = result.get("page_text", {})
    assert len(page_text) > 0
    assert any(len(str(t)) > 0 for t in page_text.values())


def test_T3_csv_freeform(fx):
    result = _load_and_classify(fx["csv_freeform"])
    assert result.get("sheet_kind") == "freeform"


def test_T4_two_sheets_tabular_plus_note(fx):
    result = _load_and_classify(fx["xlsx_2sheets_tabular_plus_note"])
    assert result.get("sheet_kind") == "tabular"
    warnings = result.get("warnings", [])
    codes = [w["code"] for w in warnings]
    assert IssueCode.SHEET_SKIPPED.value in codes


def test_T5_perf_5000x30_tabular(fx):
    result = _load_and_classify(fx["perf_5000x30"])
    assert result.get("sheet_kind") == "tabular"


def test_T6_xlsx_tiny_4row_tabular(fx):
    result = _load_and_classify(fx["xlsx_tiny_4row"])
    assert result.get("sheet_kind") == "tabular"


# ---- 2-column tabular sheet must NOT be misclassified as freeform ----

def test_two_col_tabular_not_freeform(tmp_path):
    """A 2-column sheet with numeric second column (invoice no + amount) must be tabular.
    Regression for the '<3 data columns → freeform' over-broad guard."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Invoice Number", "Amount"])
    for i in range(1, 6):
        ws.append([f"INV-{i:03d}", float(i * 100)])
    p = tmp_path / "two_col_tabular.xlsx"
    wb.save(str(p))
    result = _load_and_classify(str(p))
    assert result.get("sheet_kind") == "tabular", (
        "2-column tabular sheet (invoice no + numeric amount) must be tabular, not freeform"
    )


# ---- Two freeform sheets must remain as 2 separate entries in sheets/frames ----

def test_two_freeform_sheets_not_stacked(tmp_path):
    """Two freeform (no-header) sheets must NOT be stacked; each stays a separate entry."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Sheet1"
    ws1.append(["Invoice Number:", "INV-001"])
    ws1.append(["Total:", "500"])

    ws2 = wb.create_sheet("Sheet2")
    ws2.append(["Invoice Number:", "INV-002"])
    ws2.append(["Total:", "800"])

    p = tmp_path / "two_freeform.xlsx"
    wb.save(str(p))

    state = {"file_path": str(p), "file_type": "sheet", "sim": {}, "warnings": []}
    from app.pipeline.nodes.sheet_load import sheet_load
    load_result = sheet_load(state)
    sheets = load_result.get("sheets", [])
    frames = load_result.get("frames", {})
    # Both sheets should be present as separate entries (not stacked)
    assert len(sheets) == 2, f"Expected 2 sheet entries, got {len(sheets)}: {[s['name'] for s in sheets]}"
    assert len(frames) == 2, f"Expected 2 frame entries, got {len(frames)}"
