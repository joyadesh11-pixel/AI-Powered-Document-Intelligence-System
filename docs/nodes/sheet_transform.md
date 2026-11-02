# node: sheet_transform
**Purpose.** Apply the mapping to every row with vectorised pandas, group into invoices, and measure whether the mapping actually works. No LLM.
**Reads** `frames`, `mapping`, `mapping_retries`. **Writes** `invoices` (tier `sheet`), `parse_ok`, `mapping_feedback`, `mapping_retries`, `warnings`.
## Algorithm
1. Rename columns to schema names. Drop rows that are entirely empty.
2. Numbers: strip thousands separator, normalise decimal separator, parse via `utils/money.py` into `Decimal`. Dates: `pd.to_datetime(format=mapping.date_format, errors="coerce")`.
3. **Parse rate** per mapped numeric/date column = parsed non-null / non-null raw. Any column `< PARSE_RATE_MIN` (0.95) -> `parse_ok=False`, `mapping_feedback={column, rate, format, examples[:5]}`, `mapping_retries += 1`. Router sends back to `sheet_map` once, then to `finalize` as `needs_review` + `MAPPING_FAILED`.
4. Group: `grain=line_item` -> `groupby(group_by, sort=False)`; header fields = first non-null per group; if a header field varies inside a group -> `SHEET_GROUP_INCONSISTENT` (warning), take the most common value. Header money fields (`subtotal, tax, total`): take the value only if all non-null values in the group are equal, else leave null + warning. `grain=invoice` -> one invoice per row.
5. Never compute missing fields into real fields. Put `derived.lines_sum` (Decimal string) on the invoice for the UI and for `validate`.
6. Rows with empty group key -> not invoiced; one `UNGROUPED_ROWS` issue with count and first row numbers (this catches totals rows and stray text).
7. Keep unmapped columns in `invoice.extra` (per-invoice first value, or list for line columns). Record `source_rows`. Issues carry `{row, column}`.
**Performance.** 10,000 rows x 40 columns < 3 s; no per-row Python loops except `Decimal` parsing of mapped columns.
**Failure.** Exceptions inside pandas -> caught by wrapper -> document `failed` (bug, not data issue).
## Tests
- T1 line-grain xlsx (3 invoices, 8 lines) -> 3 invoices, correct line counts and amounts as `Decimal`.
- T2 invoice-grain csv -> one invoice per row. T3 wrong date format in mapping -> `parse_ok=False` + feedback with examples.
- T4 retry then success -> `validated_ok` path. T5 retry exhausted -> `MAPPING_FAILED`, `needs_review`.
- T6 totals row at bottom -> `UNGROUPED_ROWS`, not an invoice. T7 repeated total per line (equal) -> captured; differing -> null + warning.
- T8 European decimals `1.234,56`. T9 leading-zero invoice numbers preserved.
- T10 5,000-row perf test (record seconds). T11 `derived.lines_sum` present, `subtotal` still null when unmapped.
**Done when** T1-T11 pass.
