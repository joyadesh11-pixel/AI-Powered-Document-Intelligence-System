"""sheet_classify node — classify primary sheet as tabular or freeform.

M4: Full implementation.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.pipeline.state import State, node
from app.schemas.models import IssueCode, Severity

_WARNING_KWARGS = {"field": None, "expected": None, "got": None, "location": {}}


def _is_numeric_val(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, (int,)):
        return True
    s = str(v).strip().replace(",", "").replace(".", "").replace("-", "").replace("+", "")
    return bool(s) and s.isdigit()


def _tabular_score(sheet_meta: dict, df: Any) -> float:
    """Compute tabular score in [0, 1] for a sheet."""
    header_found = 1.0 if sheet_meta.get("header_row") is not None else 0.0
    n_rows = sheet_meta.get("n_rows", 0)
    cols = sheet_meta.get("columns", [])

    data_cols = [c for c in cols if c not in ("_row", "_sheet")]
    if len(data_cols) == 0:
        return 0.0
    if len(data_cols) == 1:
        return 0.0
    if len(data_cols) == 2 and df is not None and n_rows > 0:
        # Only force freeform for genuine label-value sheets (e.g. "Label" / "Value" CSV).
        # Test: if the second column is NOT predominantly numeric (<60%), the sheet is likely a
        # label-value format (invoice fields as rows) rather than a narrow tabular invoice.
        # A 2-column tabular sheet (e.g. "Invoice Number" / "Amount") has a numeric second
        # column and scores normally.
        second_col = data_cols[1]
        col_vals = [v for v in df[second_col].head(min(n_rows, 100))
                    if v is not None and str(v).strip() != ""]
        if not col_vals:
            return 0.0
        numeric_ratio = sum(1 for v in col_vals if _is_numeric_val(v)) / len(col_vals)
        if numeric_ratio < 0.6:
            return 0.0  # label-value format → freeform

    if n_rows == 0 or df is None:
        row_fill = 0.0
        col_type_consistency = 0.0
    else:
        row_fills = []
        for _, row in df[data_cols].head(min(n_rows, 100)).iterrows():
            non_null = sum(1 for v in row if v is not None and str(v).strip() != "")
            row_fills.append(non_null / len(data_cols))
        row_fill = sum(row_fills) / max(len(row_fills), 1)

        # col_type_consistency: share of cols with >= 80% same type
        consistent = 0
        for col in data_cols:
            vals = [v for v in df[col].head(100) if v is not None and str(v).strip() != ""]
            if not vals:
                continue
            numeric_count = sum(1 for v in vals if _is_numeric_val(v))
            ratio = numeric_count / len(vals)
            if ratio >= 0.8 or ratio <= 0.2:
                consistent += 1
        col_type_consistency = consistent / len(data_cols)

    n_rows_ok = 1.0 if n_rows >= 3 else 0.0
    score = (header_found + row_fill + col_type_consistency + n_rows_ok) / 4.0
    return score


@node("sheet_classify")
def sheet_classify(s: State) -> dict:
    sheets = s.get("sheets", [])
    frames = s.get("frames", {})
    warnings = list(s.get("warnings") or [])

    if not sheets:
        return {"sheet_kind": "freeform", "primary_sheet": None, "warnings": warnings}

    # Primary sheet = one with most rows
    primary_meta = max(sheets, key=lambda m: m.get("n_rows", 0))
    primary_name = primary_meta["name"]

    # Emit SHEET_SKIPPED for all non-primary, non-stacked sheets
    # Stacked sheets share the primary sheet's frame (same header_hash → same name in frames)
    primary_hash = primary_meta["header_hash"]
    for sm in sheets:
        if sm["name"] == primary_name:
            continue
        if sm["header_hash"] == primary_hash:
            continue  # stacked into primary
        warnings.append({
            "code": IssueCode.SHEET_SKIPPED.value,
            "severity": Severity.warning.value,
            "message": f"Sheet '{sm['name']}' skipped (not primary)",
            **_WARNING_KWARGS,
        })

    primary_df = frames.get(primary_name)
    score = _tabular_score(primary_meta, primary_df)

    # Decision
    if score >= 0.6:
        kind = "tabular"
    elif score >= 0.4 and primary_meta.get("n_rows", 0) > 50:
        kind = "tabular"
    else:
        kind = "freeform"

    if kind == "freeform":
        # Build page_text per free-form sheet
        freeform_sheets = [sm for sm in sheets
                           if sm.get("header_row") is None or kind == "freeform"]
        page_text: dict[int, str] = {}
        page_source: dict[int, str] = {}
        for i, sm in enumerate(freeform_sheets):
            df = frames.get(sm["name"])
            if df is not None:
                cols = [c for c in sm["columns"] if c not in ("_row", "_sheet")]
                parts = []
                for _, row in df.iterrows():
                    for col in cols:
                        val = row.get(col)
                        if val is not None and str(val).strip():
                            parts.append(f"{col}: {val}")
                page_text[i] = "\n".join(parts)
            else:
                page_text[i] = ""
            page_source[i] = "sheet"

        return {
            "sheet_kind": "freeform",
            "primary_sheet": primary_name,
            "page_text": page_text,
            "page_source": page_source,
            "page_count": len(freeform_sheets),
            "warnings": warnings,
        }

    return {
        "sheet_kind": "tabular",
        "primary_sheet": primary_name,
        "warnings": warnings,
    }
