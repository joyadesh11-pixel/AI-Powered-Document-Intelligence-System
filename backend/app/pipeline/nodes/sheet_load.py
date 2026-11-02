"""sheet_load node — load spreadsheet and detect header row.

M4: Full implementation.
Supports xlsx (openpyxl), xls (xlrd), csv (encoding sniff + delimiter detection).
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.pipeline.config import get_config
from app.pipeline.state import State, node
from app.schemas.models import IssueCode, Severity

_WARNING_KWARGS = {
    "field": None, "expected": None, "got": None, "location": {},
}


def _normalise_col(s: str) -> str:
    return re.sub(r" +", " ", re.sub(r"[^a-z0-9 ]+", " ", str(s).casefold())).strip()


def _header_hash(columns: list[str]) -> str:
    normalised = sorted(_normalise_col(c) for c in columns)
    return hashlib.sha256("|".join(normalised).encode()).hexdigest()[:16]


def _is_numeric_val(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, int):
        return True
    s = str(v).strip().replace(",", "").replace(".", "").replace("-", "").replace("+", "")
    return bool(s) and s.isdigit()


def _score_as_header(row_vals: list, next_row_vals: list | None) -> bool:
    n = len(row_vals)
    if n == 0:
        return False
    non_empty = [v for v in row_vals if v is not None and str(v).strip() != ""]
    if len(non_empty) / n < 0.6:
        return False
    non_numeric = sum(1 for v in non_empty if not _is_numeric_val(v))
    if non_numeric / max(len(non_empty), 1) < 0.6:
        return False
    distinct = len(set(str(v) for v in non_empty))
    if distinct / max(len(non_empty), 1) < 0.5:
        return False
    if next_row_vals is not None:
        next_ne = [v for v in next_row_vals if v is not None and str(v).strip() != ""]
        if len(next_ne) / max(n, 1) < 0.6:
            return False
    return True


def _find_header_row(rows: list[list], max_scan: int) -> int | None:
    for i, row in enumerate(rows[:max_scan]):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if _score_as_header(row, nxt):
            return i
    return None


def _clean_cell(v: Any) -> Any:
    """Normalise cell: convert datetime, strip blank to None."""
    if isinstance(v, datetime):
        if v.hour == 0 and v.minute == 0 and v.second == 0 and v.microsecond == 0:
            return v.date().isoformat()
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if v is None:
        return None
    s = str(v).strip()
    return None if s == "" else s if isinstance(v, str) else v


def _strip_xlrd_float(v: Any) -> Any:
    """Remove '.0' suffix from xlrd integral floats (e.g., 123.0 → '123')."""
    if isinstance(v, int):
        return v
    s = str(v)
    if s.endswith(".0") and s[:-2].lstrip("-+").isdigit():
        return s[:-2]
    return v


def _deduplicate_cols(cols: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for c in cols:
        if c not in seen:
            seen[c] = 0
            result.append(c)
        else:
            seen[c] += 1
            result.append(f"{c}_{seen[c] + 1}")
    return result


def _build_sheet(
    name: str,
    raw_rows: list[list],
    max_scan: int,
    engine: str = "openpyxl",
) -> tuple[dict, Any]:
    """Convert raw rows to (sheet_meta, DataFrame)."""
    cleaned: list[list] = []
    for row in raw_rows:
        new_row = [_clean_cell(v) for v in row]
        if engine == "xlrd":
            new_row = [_strip_xlrd_float(v) for v in new_row]
        cleaned.append(new_row)

    header_idx = _find_header_row(cleaned, max_scan)

    if header_idx is not None:
        h_vals = cleaned[header_idx]
        col_names = [
            str(v) if (v is not None and str(v).strip()) else f"col_{i}"
            for i, v in enumerate(h_vals)
        ]
        col_names = _deduplicate_cols(col_names)
        data_rows = cleaned[header_idx + 1:]
    else:
        max_cols = max((len(r) for r in cleaned), default=0)
        col_names = [f"col_{i}" for i in range(max_cols)]
        data_rows = cleaned

    n_cols = len(col_names)
    padded = []
    for row in data_rows:
        if len(row) < n_cols:
            row = row + [None] * (n_cols - len(row))
        padded.append(row[:n_cols])

    df = pd.DataFrame(padded, columns=col_names)
    df = df.dropna(how="all")
    df = df.dropna(axis=1, how="all")
    col_names = list(df.columns)
    n_rows = len(df)

    # Provenance: 1-based Excel row
    if header_idx is not None:
        df["_row"] = [i + header_idx + 2 for i in range(n_rows)]
    else:
        df["_row"] = [i + 1 for i in range(n_rows)]
    df["_sheet"] = name

    null_rate = {col: (df[col].isna().sum() / max(n_rows, 1)) for col in col_names}
    preview_rows = [
        {col: row[col] for col in col_names}
        for _, row in df.head(10).iterrows()
    ]

    sheet_meta = {
        "name": name,
        "columns": col_names,
        "header_row": header_idx,
        "header_hash": _header_hash(col_names),
        "n_rows": n_rows,
        "preview": {"columns": col_names, "rows": preview_rows, "null_rate": null_rate},
    }
    return sheet_meta, df


def _xlsx_gate(path: str) -> tuple[bool, bool]:
    """Scan zip XML for mergeCell / formula tags. Returns (has_merge, has_formula)."""
    has_merge = has_formula = False
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    data = zf.read(name)
                    if b"<mergeCell" in data:
                        has_merge = True
                    if b"<f>" in data or b"<f " in data:
                        has_formula = True
                    if has_merge and has_formula:
                        break
    except Exception:
        pass
    return has_merge, has_formula


def _xlsx_hidden_sheets(path: str) -> set[str]:
    """Return set of hidden sheet names parsed from workbook.xml."""
    hidden: set[str] = set()
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            wb_xml_name = next(
                (n for n in names if n.endswith("workbook.xml") and "xl/" in n), None
            )
            if wb_xml_name:
                data = zf.read(wb_xml_name).decode("utf-8", errors="replace")
                for m in re.finditer(r"<sheet\s[^>]*>", data):
                    tag = m.group(0)
                    name_m = re.search(r'\bname="([^"]*)"', tag)
                    state_m = re.search(r'\bstate="([^"]*)"', tag)
                    if name_m and state_m and state_m.group(1) in ("hidden", "veryHidden"):
                        hidden.add(name_m.group(1))
    except Exception:
        pass
    return hidden


def _load_xlsx(path: str, cfg, warnings: list[dict]) -> tuple[list[dict], dict]:
    import openpyxl

    file_size = Path(path).stat().st_size
    has_merge, has_formula = _xlsx_gate(path)
    hidden = _xlsx_hidden_sheets(path)
    use_full = (has_merge or has_formula) and file_size <= 10 * 1024 * 1024

    formula_nocache_cells: list[str] = []
    sheets_rows: dict[str, list[list]] = {}
    all_sheet_names: list[str] = []

    if use_full:
        wb = openpyxl.load_workbook(path, data_only=True)
        all_sheet_names = list(wb.sheetnames)
        for sname in all_sheet_names:
            ws = wb[sname]
            merge_map: dict[tuple[int, int], Any] = {}
            if has_merge:
                for rng in ws.merged_cells.ranges:
                    top_val = ws.cell(rng.min_row, rng.min_col).value
                    for r in range(rng.min_row, rng.max_row + 1):
                        for c in range(rng.min_col, rng.max_col + 1):
                            merge_map[(r, c)] = top_val
            rows: list[list] = []
            for row in ws.iter_rows():
                row_vals = [
                    merge_map.get((cell.row, cell.column), cell.value)
                    for cell in row
                ]
                rows.append(row_vals)
            sheets_rows[sname] = rows

        if has_formula:
            try:
                wb_form = openpyxl.load_workbook(path, data_only=False)
                for sname in wb_form.sheetnames:
                    ws_form = wb_form[sname]
                    ws_data = wb[sname]
                    for row_form in ws_form.iter_rows():
                        for cell in row_form:
                            if isinstance(cell.value, str) and cell.value.startswith("="):
                                cached = ws_data.cell(cell.row, cell.column).value
                                if cached is None:
                                    formula_nocache_cells.append(cell.coordinate)
            except Exception:
                pass
    else:
        try:
            # Fast path: read_only + values_only avoids Cell object creation
            wb_ro = openpyxl.load_workbook(path, read_only=True, data_only=True)
            all_sheet_names = list(wb_ro.sheetnames)
            for sname in all_sheet_names:
                ws_ro = wb_ro[sname]
                sheets_rows[sname] = [list(row) for row in ws_ro.iter_rows(values_only=True)]
            wb_ro.close()
        except Exception as exc:
            warnings.append({
                "code": IssueCode.FORMULA_NO_CACHE.value,
                "severity": Severity.warning.value,
                "message": f"Could not read xlsx: {exc}",
                **_WARNING_KWARGS,
            })
            return [], {}

    if formula_nocache_cells:
        warnings.append({
            "code": IssueCode.FORMULA_NO_CACHE.value,
            "severity": Severity.warning.value,
            "message": f"Formula cells with no cached value: {formula_nocache_cells[:5]}",
            **_WARNING_KWARGS,
        })

    for sname in all_sheet_names:
        if sname in hidden:
            warnings.append({
                "code": IssueCode.SHEET_SKIPPED.value,
                "severity": Severity.warning.value,
                "message": f"Sheet '{sname}' is hidden and was skipped",
                **_WARNING_KWARGS,
            })

    visible = [s for s in all_sheet_names if s not in hidden]
    all_metas: list[dict] = []
    all_frames: dict = {}
    hash_to_first: dict[str, tuple[str, dict]] = {}

    for sname in visible:
        raw = sheets_rows.get(sname, [])
        if not raw:
            continue
        meta, df = _build_sheet(sname, raw, cfg.HEADER_SCAN_ROWS, engine="openpyxl")
        h = meta["header_hash"]
        if meta["header_row"] is not None and h in hash_to_first:
            first_name, first_meta = hash_to_first[h]
            combined = pd.concat([all_frames[first_name], df], ignore_index=True)
            all_frames[first_name] = combined
            first_meta["n_rows"] = len(combined)
        else:
            hash_to_first[h] = (sname, meta)
            all_metas.append(meta)
            all_frames[sname] = df

    return all_metas, all_frames


def _detect_csv_encoding(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                f.read(8192)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"


def _detect_csv_sep(path: str, encoding: str) -> str:
    try:
        with open(path, "r", encoding=encoding, errors="replace") as f:
            sample = f.read(8192)
        lines = [ln for ln in sample.splitlines()[:8] if ln.strip()]
        if not lines:
            return ","
        best_sep, best_score = ",", -1.0
        for sep in (",", ";", "\t", "|"):
            counts = [ln.count(sep) for ln in lines]
            mean_c = sum(counts) / len(counts)
            if mean_c == 0:
                continue
            var = sum((c - mean_c) ** 2 for c in counts) / len(counts)
            score = mean_c - var
            if score > best_score:
                best_score, best_sep = score, sep
        return best_sep
    except Exception:
        return ","


def _load_csv(path: str, cfg, warnings: list[dict]) -> tuple[list[dict], dict]:
    enc = _detect_csv_encoding(path)
    sep = _detect_csv_sep(path, enc)
    try:
        df_raw = pd.read_csv(
            path, header=None, dtype=str, sep=sep, encoding=enc,
            na_filter=False, keep_default_na=False,
        )
    except Exception as exc:
        warnings.append({
            "code": IssueCode.FORMULA_NO_CACHE.value,
            "severity": Severity.warning.value,
            "message": f"Could not read csv: {exc}",
            **_WARNING_KWARGS,
        })
        return [], {}

    df_raw = df_raw.replace("", None)
    sheet_name = Path(path).stem
    rows = df_raw.values.tolist()
    meta, df = _build_sheet(sheet_name, rows, cfg.HEADER_SCAN_ROWS, engine="csv")
    return [meta], {sheet_name: df}


def _load_xls(path: str, cfg, warnings: list[dict]) -> tuple[list[dict], dict]:
    try:
        xl = pd.read_excel(
            path, sheet_name=None, header=None, dtype=object, engine="xlrd"
        )
    except Exception as exc:
        warnings.append({
            "code": IssueCode.FORMULA_NO_CACHE.value,
            "severity": Severity.warning.value,
            "message": f"Could not read .xls: {exc}",
            **_WARNING_KWARGS,
        })
        return [], {}

    all_metas: list[dict] = []
    all_frames: dict = {}
    for sname, df_raw in xl.items():
        rows = df_raw.values.tolist()
        meta, df = _build_sheet(sname, rows, cfg.HEADER_SCAN_ROWS, engine="xlrd")
        all_metas.append(meta)
        all_frames[sname] = df
    return all_metas, all_frames


@node("sheet_load")
def sheet_load(s: State) -> dict:
    path = s["file_path"]
    cfg = get_config()
    warnings = list(s.get("warnings") or [])
    ext = Path(path).suffix.lower()

    if ext == ".xlsx":
        metas, frames = _load_xlsx(path, cfg, warnings)
    elif ext == ".xls":
        metas, frames = _load_xls(path, cfg, warnings)
    elif ext == ".csv":
        metas, frames = _load_csv(path, cfg, warnings)
    else:
        try:
            metas, frames = _load_xlsx(path, cfg, warnings)
        except Exception:
            metas, frames = _load_csv(path, cfg, warnings)

    return {"sheets": metas, "frames": frames, "warnings": warnings}
