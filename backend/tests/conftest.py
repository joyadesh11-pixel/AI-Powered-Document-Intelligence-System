"""Session-scoped fixtures for real file I/O tests.

fixture_dir  — generates all M2 fixture files once per test session into a temp dir.
fx           — maps logical scenario names to concrete paths in fixture_dir.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.generate_fixtures import generate_fixtures


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate all fixture files once per session into a shared temp directory."""
    d = tmp_path_factory.mktemp("fixtures")
    generate_fixtures(d, seed=42)
    return d


@pytest.fixture(scope="session")
def fx(fixture_dir: Path) -> dict[str, Path]:
    """Map of logical scenario names to actual fixture paths."""
    return {
        # M2 scenario fixtures
        "pdf":          fixture_dir / "digital_A_001.pdf",
        "image":        fixture_dir / "valid_001.png",
        "xlsx":         fixture_dir / "tabular_001.xlsx",
        "csv":          fixture_dir / "utf8_001.csv",
        "freeform_csv": fixture_dir / "freeform_001.csv",
        "corrupt":      fixture_dir / "bad_truncated.pdf",
        "unsupported":  fixture_dir / "bad_docx.docx",
        "mixed":        fixture_dir / "mixed.pdf",
        # M4 sheet fixtures
        "xlsx_simple_linegrain":          fixture_dir / "xlsx_simple_linegrain.xlsx",
        "xlsx_invoice_grain":             fixture_dir / "xlsx_invoice_grain.xlsx",
        "csv_invoice_grain":              fixture_dir / "csv_invoice_grain.csv",
        "xlsx_odd_headers":               fixture_dir / "xlsx_odd_headers.xlsx",
        "xlsx_ambiguous_dates":           fixture_dir / "xlsx_ambiguous_dates.xlsx",
        "xlsx_multisheet_same_headers":   fixture_dir / "xlsx_multisheet_same_headers.xlsx",
        "xlsx_hidden_sheet":              fixture_dir / "xlsx_hidden_sheet.xlsx",
        "xlsx_merged_header":             fixture_dir / "xlsx_merged_header.xlsx",
        "xlsx_banner_rows":               fixture_dir / "xlsx_banner_rows.xlsx",
        "xlsx_formula_nocache":           fixture_dir / "xlsx_formula_nocache.xlsx",
        "xlsx_2sheets_tabular_plus_note": fixture_dir / "xlsx_2sheets_tabular_plus_note.xlsx",
        "xlsx_excel_dates":               fixture_dir / "xlsx_excel_dates.xlsx",
        "xlsx_tiny_4row":                 fixture_dir / "xlsx_tiny_4row.xlsx",
        "xlsx_leading_zeros":             fixture_dir / "xlsx_leading_zeros.xlsx",
        "xlsx_totals_row":                fixture_dir / "xlsx_totals_row.xlsx",
        "xlsx_header_money_equal":        fixture_dir / "xlsx_header_money_equal.xlsx",
        "xlsx_header_money_differ":       fixture_dir / "xlsx_header_money_differ.xlsx",
        "xlsx_arith_error":               fixture_dir / "xlsx_arith_error.xlsx",
        "csv_headeronly":                 fixture_dir / "csv_headeronly.csv",
        "csv_cp1252_semicolon":           fixture_dir / "csv_cp1252_eu.csv",
        "perf_5000x30":                   fixture_dir / "perf_5000x30.xlsx",
        # Existing sheet fixtures
        "xls_legacy":   fixture_dir / "legacy_001.xls",
        "csv_utf8":     fixture_dir / "utf8_001.csv",
        "xlsx_freeform_one_invoice": fixture_dir / "freeform_001.xlsx",
        "csv_freeform": fixture_dir / "freeform_001.csv",
        # M5 scan image fixtures
        "scan_clean":    fixture_dir / "scan_clean.png",
        "scan_mild":     fixture_dir / "scan_mild.png",
        "scan_moderate": fixture_dir / "scan_moderate.png",
        "blurry_severe": fixture_dir / "blurry_severe.png",
        "rotated_5deg":  fixture_dir / "rotated_5deg.png",
        "rotated_90":    fixture_dir / "rotated_90.png",
        "tiny_300px":    fixture_dir / "tiny_300px.png",
        "exif_rotated":  fixture_dir / "exif_rotated.jpg",
        "scan_3frame":   fixture_dir / "scan_3frame.tiff",
        "blank":         fixture_dir / "blank.png",
        "scanned_pdf":   fixture_dir / "scanned.pdf",
        # M7 scanned PDF fixtures
        "scanned_clean":    fixture_dir / "scanned_clean.pdf",
        "scanned_mild":     fixture_dir / "scanned_mild.pdf",
        "scanned_moderate": fixture_dir / "scanned_moderate.pdf",
        "scanned_severe":   fixture_dir / "scanned_severe.pdf",
        "scanned_rotated":  fixture_dir / "scanned_rotated.pdf",
        "scanned_3page":    fixture_dir / "scanned_3page.pdf",
        # M7 multi-invoice PDF fixtures
        "multi_inv_001": fixture_dir / "multi_inv_001.pdf",
        "multi_inv_002": fixture_dir / "multi_inv_002.pdf",
        "multi_inv_003": fixture_dir / "multi_inv_003.pdf",
        # M7 injected-error fixtures
        "wrong_total_001":   fixture_dir / "wrong_total_001.pdf",
        "wrong_total_002":   fixture_dir / "wrong_total_002.pdf",
        "wrong_total_003":   fixture_dir / "wrong_total_003.pdf",
        "dup_invnum_001":    fixture_dir / "dup_invnum_001.pdf",
        "dup_invnum_002":    fixture_dir / "dup_invnum_002.pdf",
        "missing_invnum_001": fixture_dir / "missing_invnum_001.pdf",
        "missing_invnum_002": fixture_dir / "missing_invnum_002.pdf",
    }


@pytest.fixture(scope="session")
def gt(fixture_dir: Path) -> dict[str, dict]:
    """Ground-truth index keyed by filename."""
    import json
    with open(fixture_dir / "ground_truth.json") as f:
        entries = json.load(f)
    return {e["file"]: e for e in entries}
