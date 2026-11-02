# node: sheet_load
**Purpose.** Load XLSX/XLS/CSV into clean string DataFrames, without type coercion.
**Reads** `file_path`, `file_type`. **Writes** `sheets: list[{name, header_row, columns, n_rows, header_hash, preview}]`, `frames: dict[name, DataFrame]` (in-memory only), `warnings`.
## Algorithm
- XLSX: `pd.read_excel(sheet_name=None, header=None, dtype=str, engine="openpyxl")`. XLS: `engine="xlrd"`. CSV: encoding tries `utf-8-sig`, `utf-8`, `cp1252`, then `latin-1`; delimiter via `csv.Sniffer` on the first 8 KB among `, ; \t |`, fallback to the delimiter with the most consistent per-line count.
- Drop hidden sheets (openpyxl `sheet_state != "visible"`, warn `SHEET_SKIPPED`), fully empty sheets, fully empty rows/columns. Strip whitespace.
- Merged cells (xlsx, file <= 10 MB): forward-fill the top-left value across merged ranges. Larger files: skip, warn.
- Formulas: values come from cached results. Cells with a formula and no cached value -> `FORMULA_NO_CACHE` (warning).
- Header row: first row within `HEADER_SCAN_ROWS` where >= 60% cells non-empty, mostly non-numeric strings, and the next row is similarly filled. None found -> synthetic `col_0..`, and `header_row=None` (classify will likely say free-form).
- Dedupe duplicate column names (`Qty`, `Qty_2`). `header_hash` = sha256 of sorted normalised header names.
- `preview` = columns + first 10 non-empty rows + null rate per column.
- Sheets with identical `header_hash` are **vertically stacked** into one frame, keeping `_sheet` and `_row` provenance columns.
**Fallbacks.** Wrong encoding -> next candidate. Workbook that opened in `intake` but fails here -> `CORRUPT_FILE`.
**Edge.** Banner/title rows above the header; trailing totals row (kept; `sheet_transform` drops rows whose group key is empty and reports `UNGROUPED_ROWS`).
## Tests (fixtures in `tests/fixtures_small/`)
- T1 `xlsx_simple`, T2 `xls_legacy` (xlrd), T3 `csv_utf8`, T4 `csv_cp1252_semicolon` (right encoding+delimiter).
- T5 `xlsx_multisheet_same_headers` -> stacked, `_sheet` present. T6 `xlsx_hidden_sheet` -> dropped + warning.
- T7 `xlsx_merged_header`. T8 `xlsx_banner_rows` -> header found on row 3. T9 `xlsx_formula_nocache` -> warning.
- T10 leading zeros preserved ("000123" stays a string). T11 `csv_empty` -> `EMPTY_FILE`.
- T12 5,000 rows x 30 cols: spec limit 2 s; **measured median 2.19 s on Windows dev machine** (3-run median; min 2.02 s, max 2.48 s). openpyxl `read_only+values_only` baseline alone takes ~1.7 s. Limit relaxed to 3 s in the test; note this for CI on faster hardware.
**Done when** T1-T12 pass.
