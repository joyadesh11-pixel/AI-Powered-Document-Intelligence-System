# 05. Testing and evaluation

## Test layers
| Layer | Folder | Marker | LLM | Purpose |
|---|---|---|---|---|
| Unit | `tests/unit/` mirrors `nodes/`, `validation/`, `utils/` | default | `FakeLLM` | each node's spec test list |
| Architecture | `tests/unit/test_architecture.py` | default | - | AST scan: `pipeline/` imports none of db/api/services/workers/sqlalchemy/fastapi/celery/redis; no `float(` in `validation/` and `utils/money.py` |
| Integration | `tests/integration/` | default | `FakeLLM` + real fixtures | the 12 scenarios from the dry run, now real; assert status and the node path |
| API | `tests/api/` | default | `FakeLLM`, test DB, `CELERY_TASK_ALWAYS_EAGER` | upload/validation/limits, status, results, search, corrections, reprocess, auth |
| Live | `tests/live/` | `live` | real | <= 5 calls: digital, scan, vision, sheet-map |
Run: `pytest -m "not live"` (CI/fast) and `pytest -m live` (manual, record output in the report).
Coverage target: report `pytest --cov` number honestly; do not chase 100%.

### The 12 integration scenarios (names kept from the dry run)
digital PDF clean -> validated_ok · scanned image -> validated_ok · mixed PDF + low-conf unit -> validated_ok via vision ·
arithmetic error fixed by vision -> validated_ok · arithmetic error persists -> needs_review ·
xlsx tabular -> validated_ok · xlsx mapping bad once -> validated_ok · xlsx mapping bad twice -> needs_review ·
csv free-form -> validated_ok · corrupt -> failed · unsupported -> failed · injected exception -> failed.
Add: xlsx arithmetic error -> needs_review with **no** vision call · duplicate of #1 -> needs_review · missing invoice number -> needs_review.

## Synthetic dataset (`scripts/generate_fixtures.py --seed 42`)
Ground truth known by construction, deterministic seed. **State in the report that the dataset is synthetic.**
Libraries: `reportlab` (PDF), `Pillow`/`numpy`/`opencv` (degradation), `pandas`/`openpyxl`/`xlwt` (sheets), `faker`.
Three invoice templates: (A) classic bordered table; (B) borderless modern; (C) multi-page with continuation, "Page x of y", repeated header.
Varied: supplier names, currencies (USD, EUR with decimal comma, GBP, BDT, JPY zero-decimal), date formats (ISO, dd/mm/yyyy, "3 Nov 2026"), 1-25 line items, tax rates 0/5/10/15/20%.

| Group | Files | Notes |
|---|---|---|
| Digital PDF | 20 | templates A/B/C evenly |
| Scanned PDF | 6 | rasterised; mild/moderate/severe: blur, rotation 0-8 degrees, noise, JPEG artefacts |
| Images | 10 | JPG/PNG/TIFF(multi-frame), quality tiers. M5 scan fixtures (all from a single reportlab invoice rasterised at 250 DPI): `scan_clean` (unmodified, Laplacian var≈341), `scan_mild` (GaussianBlur σ=0.5, rot=1.5°, noise σ=10, JPEG q=72; raw var≈104, processed≈71 — above BLUR_VAR_MIN so not flagged), `scan_moderate` (σ=0.5, rot=3.5°, noise σ=12, JPEG q=68; raw var≈106, processed≈72 — the "moderate" tier is **lightly blurred** because higher σ would drop the processed variance below the OCR quality gate; the main distinguishing factor is the larger rotation and slightly heavier noise/compression), `blurry_severe` (σ=20, var≈0.1 — flagged), `tiny_300px` (300×300 px — flagged for side_px), `blank` (white 800×1100, var=0), `exif_rotated` (EXIF Orientation=6, corrected by `exif_transpose`), `rotated_5deg` (5° CW, deskewed to <1.5° residual), `rotated_90` (orientation-retry recovers), `scan_3frame` (3-frame TIFF). |
| XLSX | 5 | line-grain multi-invoice (200 rows), invoice-grain, multi-sheet same headers, free-form one invoice, odd-header layout |
| XLS | 1 | legacy |
| CSV | 3 | utf-8 comma, cp1252 semicolon, free-form |
| Multi-invoice PDF | 3 | 5-10 invoices each, tests `segment` |
| Injected errors | 7 | wrong total x3, duplicate of an earlier invoice x2, missing invoice number x2 |
| Bad files | 5 | truncated pdf, corrupt xlsx, `.docx`, encrypted pdf, 0-byte |
| Large sheet | 1 | 5,000 rows x 30 columns (perf only) |
About 60 files, 100+ invoices. Each fixture has an entry in `eval/ground_truth.json`:
```json
{"file":"digital_A_007.pdf","group":"digital_pdf","expect_status":"validated_ok",
 "injected":null,
 "invoices":[{"invoice_number":"INV-2026-0412","invoice_date":"2026-11-03","supplier_name":"Acme Ltd",
   "customer_name":"Bolt Co","subtotal":"90.00","tax":"9.00","total":"99.00","currency":"USD",
   "line_items":[{"description":"Widget","quantity":"10","unit_price":"5.00","amount":"50.00"}]}]}
```
Injected-error entries add `"injected":{"code":"ARITH_TOTAL"}`; bad files have `"expect_error_code"`.

## Metrics (all computed by `scripts/evaluate.py`, nothing typed by hand)
**Matching.** Predicted invoices to GT invoices by `invoice_number` (fallback: best total+supplier match). Unmatched GT = missed invoice; unmatched prediction = extra.
**Normalisation for comparison.** Strings: casefold, collapse whitespace. Amounts: `Decimal` equal. Dates: `date` equal.
1. **Header field accuracy** per field (`invoice_number, invoice_date, supplier_name, customer_name, subtotal, tax, total, currency`) and micro average. Each prediction classified: *correct*, *correct-null* (GT null, pred null), *omission* (GT value, pred null), *wrong* (both non-null, differ), *hallucination* (GT null, pred non-null). Report all five rates per field.
2. **Line items** (separately, expected weaker): match lines within an invoice by description similarity then amount; report precision, recall, F1, and accuracy of `quantity, unit_price, amount` on matched lines.
3. **Invoice fully correct rate** (all header fields and all lines).
4. **Silent error rate (the safety metric):** among invoices with status `validated_ok`, share that are not fully correct. Target: low and honestly reported with a count and a list of the failures.
5. **Review rate:** share of invoices `needs_review`; split into *correctly flagged* (pipeline output actually wrong or injected error) and *unnecessary review*.
6. **Validation detection:** for injected errors, recall of the expected code; false-positive rate on clean invoices.
7. **Status accuracy:** confusion matrix `expect_status` vs actual (file level).
8. **Success / failure:** success rate = files reaching `validated_ok|needs_review` among files expected to be processable; failure rate = `failed` among those; plus "bad files handled correctly" (expected failures that failed with the right `error_code`, and no crashes).
9. **Latency:** per file p50/p95/mean overall and by `file_type` and `tier`; per-node breakdown from `history`; pages/minute at concurrency 1 and 4; 5,000-row sheet seconds.
10. **Tier usage:** share of invoices by tier, vision escalation rate, LLM calls and tokens per document (from `llm_usage`).
11. **Sheet efficiency:** share of sheet layouts that needed 0 LLM calls (synonym pass + cache).
12. **Segmentation:** boundary accuracy on multi-invoice PDFs.
Run the full evaluation twice and report the difference (LLM nondeterminism check) if time allows.

## Outputs
`eval/reports/<ts>/`: `results.json` (raw), `per_file.csv`, `report.md` (tables below), `confusion.csv`.

## Report skeleton (`docs/evaluation-report.md` is a copy of the final `report.md`)
1. Setup: dataset description (synthetic, how degraded, seed), models/versions, machine, date, commit hash.
2. Results tables for metrics 1-12.
3. Error analysis: list every silent error and every hallucination with file, field, cause.
4. What the numbers do **not** show: synthetic layouts, limited languages, no handwriting, small sample.
5. Reproduce: exact commands.
