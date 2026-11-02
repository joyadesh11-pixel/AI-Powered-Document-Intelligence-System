# node: sheet_classify
**Purpose.** Route by shape: big tabular data stays in pandas; a small free-form invoice laid out in cells goes to the LLM as text.
**Reads** `sheets`, `frames`. **Writes** `sheet_kind` (tabular|freeform), `primary_sheet`, and for free-form: `page_text[i]` (one "page" per sheet), `page_source[i]="sheet"`, `page_count`.
## Algorithm
- Primary sheet = most non-empty rows after cleaning. Other sheets of the other kind -> `SHEET_SKIPPED` warning (known limitation).
- Tabular score (each 0..1, averaged): header found; mean row fill >= 0.6; column type consistency (>= 80% of non-null values in a column parse as the same kind: number/date/text); `n_rows >= 3`.
- Free-form signals: row fill < 0.35; many rows whose first cell ends with ":" or is a known label (Invoice No, Date, Total, Supplier...) followed by a value.
- Decide: tabular score >= 0.6 -> tabular. Else free-form. Tie-break: `n_rows > 50` -> tabular.
- Free-form rendering: each non-empty row as tab-separated cells, with `=== SHEET name ===` markers.
**Failure.** None expected; default to tabular and let `sheet_map` fail loudly if wrong.
## Tests
- T1 `xlsx_simple` (200 rows) -> tabular. T2 `xlsx_freeform_one_invoice` -> freeform with rendered text containing the invoice number.
- T3 `csv_freeform` -> freeform. T4 `xlsx_2sheets_one_tabular_one_note` -> tabular + `SHEET_SKIPPED`.
- T5 5,000-row sheet -> tabular. T6 tiny 4-row tabular list -> tabular.
**Done when** T1-T6 pass.

## Amendment: 2-column sheet classification
The original `< 3 data columns → score 0.0` guard was over-broad: it forced any 2-column
tabular sheet (e.g. "Invoice Number" + "Amount") to be classified as freeform.

**Revised rule** (implemented in M4 verification pass):
- 1 data column → 0.0 (always freeform)
- 2 data columns AND second column numeric ratio **< 60%** → 0.0 (label-value format)
- 2 data columns AND second column numeric ratio **≥ 60%** → score normally (tabular)
- ≥ 3 data columns → score normally

Rationale: a genuine label-value CSV (rows like "Invoice Number" / "INV-001", "Total" / "110.00")
has a mixed-type second column (~40% numeric). A 2-column tabular invoice sheet (invoice IDs +
amounts) has a ≥ 60% numeric second column and must not be misclassified.
