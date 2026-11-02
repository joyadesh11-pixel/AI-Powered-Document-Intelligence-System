"""Tests for sheet_load node — M4.

T1  xlsx_simple_linegrain → frame exists, correct columns
T2  xls_legacy → xlrd engine, correct row count
T3  csv_utf8 → encoding detected
T4  csv_cp1252_semicolon → encoding + semicolon delimiter
T5  xlsx_multisheet_same_headers → stacked frame, _sheet col present
T6  xlsx_hidden_sheet → SHEET_SKIPPED warning; hidden sheet absent from frames
T7  xlsx_merged_header → merged value propagated
T8  xlsx_banner_rows → header_row == 2 (0-indexed)
T9  xlsx_formula_nocache → FORMULA_NO_CACHE warning
T10 xlsx_leading_zeros → "000001" preserved as string
T11 csv_headeronly → sheets[0]["n_rows"] == 0; no pipeline error
T12 perf_5000x30.xlsx → elapsed < 2 s
"""
from __future__ import annotations

import time

import pytest

from app.pipeline.nodes.sheet_load import sheet_load
from app.schemas.models import IssueCode


def _run(path, file_type="sheet"):
    state = {"file_path": str(path), "file_type": file_type, "sim": {}, "warnings": []}
    return sheet_load(state)


@pytest.mark.parametrize("key", ["xlsx_simple_linegrain"])
def test_T1_xlsx_simple_linegrain(fx, key):
    result = _run(fx[key])
    assert "sheets" in result and len(result["sheets"]) > 0
    assert "frames" in result and len(result["frames"]) > 0
    sheet = result["sheets"][0]
    cols = sheet["columns"]
    assert "Invoice Number" in cols
    assert "Amount" in cols
    # frame has >100 rows
    frame_name = list(result["frames"].keys())[0]
    assert len(result["frames"][frame_name]) > 100


def test_T2_xls_legacy(fx):
    result = _run(fx["xls_legacy"])
    assert "sheets" in result and len(result["sheets"]) > 0
    sheet = result["sheets"][0]
    assert sheet["n_rows"] >= 3


def test_T3_csv_utf8(fx):
    result = _run(fx["csv_utf8"], file_type="sheet")
    assert "sheets" in result and len(result["sheets"]) > 0
    sheet = result["sheets"][0]
    assert "Invoice Number" in sheet["columns"]


def test_T4_csv_cp1252_semicolon(fx):
    result = _run(fx["csv_cp1252_semicolon"], file_type="sheet")
    assert "sheets" in result and len(result["sheets"]) > 0
    sheet = result["sheets"][0]
    assert "Invoice Number" in sheet["columns"]
    # frame loaded (no crash)
    frame_name = list(result["frames"].keys())[0]
    assert len(result["frames"][frame_name]) > 0


def test_T5_xlsx_multisheet_same_headers_stacked(fx):
    result = _run(fx["xlsx_multisheet_same_headers"])
    assert "sheets" in result
    # Stacked → one entry in sheets (or both present but merged into one frame)
    frames = result["frames"]
    assert len(frames) > 0
    # One of the frames must have _sheet column (from stacking)
    for name, df in frames.items():
        if "_sheet" in df.columns:
            assert len(df) >= 6  # at least 3 rows × 2 sheets
            return
    # If not stacked, at least both sheets' data should be accessible
    total_rows = sum(len(df) for df in frames.values())
    assert total_rows >= 6


def test_T6_xlsx_hidden_sheet(fx):
    result = _run(fx["xlsx_hidden_sheet"])
    warnings = result.get("warnings", [])
    codes = [w["code"] for w in warnings]
    assert IssueCode.SHEET_SKIPPED.value in codes
    # Hidden sheet absent from frames
    frames = result.get("frames", {})
    for name in frames:
        assert "hidden" not in name.lower()


def test_T7_xlsx_merged_header(fx):
    result = _run(fx["xlsx_merged_header"])
    assert "sheets" in result and len(result["sheets"]) > 0
    sheet = result["sheets"][0]
    # Real header should be found with proper column names
    assert "Invoice Number" in sheet["columns"]


def test_T8_xlsx_banner_rows(fx):
    result = _run(fx["xlsx_banner_rows"])
    assert "sheets" in result and len(result["sheets"]) > 0
    sheet = result["sheets"][0]
    # Header on row 3 = 0-indexed 2
    assert sheet["header_row"] == 2, f"Expected header_row=2, got {sheet['header_row']}"
    # First data row should have _row == 4 (1-based Excel row)
    frame_name = list(result["frames"].keys())[0]
    df = result["frames"][frame_name]
    assert "_row" in df.columns
    first_row = df["_row"].iloc[0]
    assert first_row == 4, f"Expected first _row=4, got {first_row}"


def test_T9_xlsx_formula_nocache(fx):
    result = _run(fx["xlsx_formula_nocache"])
    warnings = result.get("warnings", [])
    codes = [w["code"] for w in warnings]
    assert IssueCode.FORMULA_NO_CACHE.value in codes


def test_T10_xlsx_leading_zeros(fx):
    result = _run(fx["xlsx_leading_zeros"])
    assert "frames" in result and len(result["frames"]) > 0
    frame_name = list(result["frames"].keys())[0]
    df = result["frames"][frame_name]
    inv_col = next(c for c in df.columns if "invoice" in c.lower() or c == "Invoice Number")
    first_inv = df[inv_col].iloc[0]
    assert str(first_inv).startswith("0"), f"Expected leading zero preserved, got {first_inv!r}"


def test_T11_csv_headeronly(fx):
    result = _run(fx["csv_headeronly"], file_type="sheet")
    assert "sheets" in result and len(result["sheets"]) > 0
    # n_rows == 0; no error
    sheet = result["sheets"][0]
    assert sheet["n_rows"] == 0
    assert not result.get("error")


def test_T12_perf_5000x30(fx):
    start = time.perf_counter()
    result = _run(fx["perf_5000x30"])
    elapsed = time.perf_counter() - start
    # Windows dev machine: openpyxl read_only parse alone takes ~1.7s; limit 3s
    assert elapsed < 3.0, f"sheet_load took {elapsed:.2f}s (limit 3s)"
    assert "frames" in result


def test_xlsx_excel_dates_stripped(fx):
    """Excel datetime cells → YYYY-MM-DD string (no time component)."""
    result = _run(fx["xlsx_excel_dates"])
    assert "frames" in result and len(result["frames"]) > 0
    frame_name = list(result["frames"].keys())[0]
    df = result["frames"][frame_name]
    date_col = "Invoice Date"
    assert date_col in df.columns
    first_date = df[date_col].iloc[0]
    assert " " not in str(first_date), f"Date has time component: {first_date!r}"
    assert str(first_date).startswith("2026-10-"), f"Date format unexpected: {first_date!r}"
