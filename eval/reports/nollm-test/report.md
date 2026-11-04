# Evaluation report

## 1. Setup

- **Dataset: synthetic.** 93 generated fixtures with 73 ground-truth invoices (`scripts/generate_fixtures.py --seed 42`). Ground truth is known by construction. Scans are produced by rasterising generated PDFs and degrading them (blur, rotation, noise, JPEG artefacts).
- **Execution mode:** in-process: app.pipeline.graph.process(); no API, DB or Celery queue. Upload limits, auth, DB persistence and worker concurrency are therefore *not* part of these numbers.
- **Models:** text `gemini-3.5-flash`, vision `gemini-3.5-flash` (LLM concurrency per document: 1).
- **Machine:** Windows 11, Intel64 Family 6 Model 141 Stepping 1, GenuineIntel, 12 logical CPUs; Python 3.12.9.
- **Packages:** langgraph 1.1.2, langchain-google-genai 2.1.12, pydantic 2.14.2, pymupdf 1.28.2, rapidocr-onnxruntime 1.4.4, pandas 2.3.3.
- **Date:** 2026-11-04T15:15:30+06:00; **commit:** `e597bf4 (+uncommitted changes)`.
- **Subset that ran:** 46 of 93 files ran (46 are scored for accuracy). ran: 46, skipped_llm_required: 32, skipped_not_selected: 15.
- **Every rate below is computed only over files that ran** (state `ran`). Skipped files are listed in section 2.

## 2. Coverage: which files ran, which were skipped and why

| group | docs/05 group | ran | skipped_llm_required | skipped_not_selected |
|---|---|---|---|---|
| bad_files | Bad files | 5 | 0 | 0 |
| csv | CSV | 2 | 1 | 0 |
| digital_pdf | Digital PDF | 5 | 0 | 15 |
| edge | Intake edge cases | 1 | 2 | 0 |
| image | Images | 0 | 3 | 0 |
| image_m5 | Images | 2 | 8 | 0 |
| injected | Injected errors | 7 | 0 | 0 |
| multi_invoice | Multi-invoice PDF | 1 | 2 | 0 |
| scanned_pdf | Scanned PDF | 1 | 5 | 0 |
| sheet_m4 | XLSX/CSV (sheet edge cases) | 17 | 3 | 0 |
| sheet_m4_perf | Large sheet | 1 | 0 | 0 |
| triage | PDF triage edge cases | 1 | 8 | 0 |
| xls | XLS | 1 | 0 | 0 |
| xlsx | XLSX | 2 | 0 | 0 |

<details><summary>Per-file state</summary>

| file | group | state | reason |
|---|---|---|---|
| digital_A_001.pdf | digital_pdf | ran | null |
| digital_A_002.pdf | digital_pdf | ran | null |
| digital_A_003.pdf | digital_pdf | ran | null |
| digital_A_004.pdf | digital_pdf | ran | null |
| digital_A_005.pdf | digital_pdf | ran | null |
| digital_A_006.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_A_007.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_B_001.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_B_002.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_B_003.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_B_004.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_B_005.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_B_006.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_B_007.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_C_001.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_A_008.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_A_009.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_A_010.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_B_008.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| digital_B_009.pdf | digital_pdf | skipped_not_selected | --max-llm-files reached |
| valid_001.png | image | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| valid_001.jpg | image | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| multiframe_001.tiff | image | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| tabular_001.xlsx | xlsx | ran | null |
| freeform_001.xlsx | xlsx | ran | null |
| legacy_001.xls | xls | ran | null |
| utf8_001.csv | csv | ran | null |
| freeform_001.csv | csv | skipped_llm_required | --no-llm: file needs an LLM call |
| cp1252_001.csv | csv | ran | null |
| scanned.pdf | triage | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| mixed.pdf | triage | ran | null |
| garbled_cid.pdf | triage | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| garbled_pua.pdf | triage | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| blank_page.pdf | triage | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| scan_with_pagenum.pdf | triage | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| scan_with_pagenum_short.pdf | triage | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| borderless_table.pdf | triage | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| two_column.pdf | triage | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| bad_truncated.pdf | bad_files | ran | null |
| bad_encrypted.pdf | bad_files | ran | null |
| bad_empty.bin | bad_files | ran | null |
| bad_docx.docx | bad_files | ran | null |
| bad_corrupt.xlsx | bad_files | ran | null |
| edge_truncated.png | edge | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| edge_pdf_as_jpg.jpg | edge | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| edge_zipbomb.xlsx | edge | ran | null |
| xlsx_simple_linegrain.xlsx | sheet_m4 | ran | null |
| xlsx_invoice_grain.xlsx | sheet_m4 | ran | null |
| csv_invoice_grain.csv | sheet_m4 | ran | null |
| xlsx_odd_headers.xlsx | sheet_m4 | ran | null |
| xlsx_ambiguous_dates.xlsx | sheet_m4 | skipped_llm_required | --no-llm: file needs an LLM call |
| xlsx_multisheet_same_headers.xlsx | sheet_m4 | ran | null |
| xlsx_hidden_sheet.xlsx | sheet_m4 | ran | null |
| xlsx_merged_header.xlsx | sheet_m4 | ran | null |
| xlsx_banner_rows.xlsx | sheet_m4 | ran | null |
| xlsx_formula_nocache.xlsx | sheet_m4 | skipped_llm_required | --no-llm: file needs an LLM call |
| xlsx_2sheets_tabular_plus_note.xlsx | sheet_m4 | ran | null |
| xlsx_excel_dates.xlsx | sheet_m4 | ran | null |
| xlsx_tiny_4row.xlsx | sheet_m4 | ran | null |
| xlsx_leading_zeros.xlsx | sheet_m4 | ran | null |
| xlsx_totals_row.xlsx | sheet_m4 | ran | null |
| xlsx_header_money_equal.xlsx | sheet_m4 | ran | null |
| xlsx_header_money_differ.xlsx | sheet_m4 | ran | null |
| xlsx_arith_error.xlsx | sheet_m4 | ran | null |
| csv_headeronly.csv | sheet_m4 | skipped_llm_required | --no-llm: file needs an LLM call |
| csv_cp1252_eu.csv | sheet_m4 | ran | null |
| perf_5000x30.xlsx | sheet_m4_perf | ran | null |
| scan_clean.png | image_m5 | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| scan_mild.png | image_m5 | ran | null |
| scan_moderate.png | image_m5 | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| blurry_severe.png | image_m5 | ran | null |
| rotated_5deg.png | image_m5 | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| rotated_90.png | image_m5 | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| tiny_300px.png | image_m5 | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| exif_rotated.jpg | image_m5 | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| scan_3frame.tiff | image_m5 | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| blank.png | image_m5 | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| scanned_clean.pdf | scanned_pdf | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| scanned_mild.pdf | scanned_pdf | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| scanned_moderate.pdf | scanned_pdf | ran | null |
| scanned_severe.pdf | scanned_pdf | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| scanned_rotated.pdf | scanned_pdf | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| scanned_3page.pdf | scanned_pdf | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| multi_inv_001.pdf | multi_invoice | ran | null |
| multi_inv_002.pdf | multi_invoice | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| multi_inv_003.pdf | multi_invoice | skipped_llm_required | --no-llm: pdf/image always needs the LLM |
| wrong_total_001.pdf | injected | ran | null |
| wrong_total_002.pdf | injected | ran | null |
| wrong_total_003.pdf | injected | ran | null |
| dup_invnum_001.pdf | injected | ran | null |
| dup_invnum_002.pdf | injected | ran | null |
| missing_invnum_001.pdf | injected | ran | null |
| missing_invnum_002.pdf | injected | ran | null |

</details>

## 3. Results

### 1. Header field accuracy (matched invoices; scored files only)

| field | n | accuracy | correct | correct_null | omission | wrong | hallucination |
|---|---|---|---|---|---|---|---|
| invoice_number | 38 | 100.0% | 36 (94.7%) | 2 (5.3%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| invoice_date | 38 | 94.7% | 36 (94.7%) | 0 (0.0%) | 2 (5.3%) | 0 (0.0%) | 0 (0.0%) |
| supplier_name | 38 | 94.7% | 36 (94.7%) | 0 (0.0%) | 2 (5.3%) | 0 (0.0%) | 0 (0.0%) |
| customer_name | 38 | 94.7% | 36 (94.7%) | 0 (0.0%) | 0 (0.0%) | 2 (5.3%) | 0 (0.0%) |
| subtotal | 38 | 86.8% | 28 (73.7%) | 5 (13.2%) | 4 (10.5%) | 1 (2.6%) | 0 (0.0%) |
| tax | 38 | 89.5% | 29 (76.3%) | 5 (13.2%) | 4 (10.5%) | 0 (0.0%) | 0 (0.0%) |
| total | 38 | 84.2% | 32 (84.2%) | 0 (0.0%) | 4 (10.5%) | 2 (5.3%) | 0 (0.0%) |
| currency | 38 | 100.0% | 38 (100.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| **micro average** | 304 | 93.1% | 271 (89.1%) | 12 (3.9%) | 16 (5.3%) | 5 (1.6%) | 0 (0.0%) |

Accuracy = (correct + correct-null) / n. A missed invoice counts as an omission for each non-null GT field. Null-GT fields are rare in this dataset (see section 5), so correct-null and hallucination rates rest on a small n.

### 2. Line items

| precision | recall | F1 | matched lines | quantity acc. | unit_price acc. | amount acc. |
|---|---|---|---|---|---|---|
| 96.0% | 100.0% | 98.0% | 264 | 93.6% | 91.7% | 97.7% |

TP 264, FP 11, FN 0.

### 3. Invoice fully correct

**22 / 38 = 57.9%** of ground-truth invoices (all 8 header fields and all lines; a missed invoice counts as not correct).

### 4. Silent error rate (safety metric)

**1 / 21 = 4.8%** of `validated_ok` invoices are not fully correct. 1 failing field row(s); the full list is in section 4 below.

### 5. Review rate

| invoices | needs_review | review rate | correctly flagged | unnecessary review |
|---|---|---|---|---|
| 38 | 17 | 44.7% | 15 | 2 |

*Correctly flagged* = output not fully correct, or the file carries an injected error. *Unnecessary* = output fully correct and no injected error.

### 6. Validation detection

| injected invoices | detected | recall | clean invoices | false positives | FP rate |
|---|---|---|---|---|---|
| 7 | 7 | 100.0% | 22 | 2 | 9.1% |

| injected code | n | detected |
|---|---|---|
| ARITH_TOTAL | 3 | 3 |
| DUPLICATE_INVOICE | 2 | 2 |
| MISSING_REQUIRED | 2 | 2 |

False positive = a fully correct invoice in a non-injected file that received an error-severity issue.

### 7. Status accuracy (file level)

Accuracy 82.6% over 46 files. Rows = expected, columns = actual (also in `confusion.csv`).

| expected \ actual | validated_ok | needs_review | failed |
|---|---|---|---|
| validated_ok | 10 | 7 | 0 |
| needs_review | 1 | 22 | 0 |
| failed | 0 | 0 | 6 |

### 8. Success / failure

| processable files | success | failed | success rate | failure rate | bad files handled correctly | crashes |
|---|---|---|---|---|---|---|
| 40 | 40 | 0 | 100.0% | 0.0% | 5 / 6 | 0 |

Success = `validated_ok` or `needs_review` among files expected to be processable. Handled correctly = expected failure with the right `error_code` and no crash.

### 9. Latency (seconds per file, wall clock)

| slice | n | p50 | p95 | mean |
|---|---|---|---|---|
| overall | 46 | 0.09 | 70.27 | 14.45 |
| file_type=image | 2 | 18.73 | 21.01 | 18.73 |
| file_type=pdf | 15 | 68.38 | 70.88 | 40.00 |
| file_type=sheet | 23 | 0.08 | 5.04 | 1.12 |
| file_type=unknown | 6 | 0.06 | 0.92 | 0.25 |
| tier=none | 6 | 0.06 | 0.92 | 0.25 |
| tier=ocr | 1 | 16.20 | 16.20 | 16.20 |
| tier=parser | 14 | 68.45 | 70.88 | 42.17 |
| tier=sheet | 23 | 0.08 | 5.04 | 1.12 |
| tier=vision | 2 | 15.41 | 20.68 | 15.41 |

Per node (summed per file, from `history`):

| node | files | mean s | p50 | p95 | total s |
|---|---|---|---|---|---|
| fail | 6 | 0.00 | 0.00 | 0.00 | 0.00 |
| finalize | 40 | 0.00 | 0.00 | 0.00 | 0.00 |
| image_prep | 4 | 0.49 | 0.18 | 1.25 | 1.97 |
| intake | 46 | 0.04 | 0.01 | 0.27 | 1.85 |
| llm_extract | 18 | 4.51 | 3.42 | 12.50 | 81.25 |
| ocr | 4 | 0.84 | 0.00 | 2.86 | 3.37 |
| pdf_text | 15 | 0.01 | 0.01 | 0.03 | 0.21 |
| pdf_triage | 15 | 0.01 | 0.00 | 0.03 | 0.12 |
| segment | 18 | 0.00 | 0.00 | 0.00 | 0.00 |
| sheet_classify | 23 | 0.00 | 0.00 | 0.00 | 0.03 |
| sheet_load | 23 | 0.09 | 0.01 | 0.02 | 2.11 |
| sheet_map | 22 | 0.63 | 0.00 | 0.01 | 13.76 |
| sheet_transform | 22 | 0.04 | 0.00 | 0.02 | 0.96 |
| validate | 40 | 0.01 | 0.00 | 0.00 | 0.42 |
| vision_extract | 10 | 55.15 | 65.11 | 65.93 | 551.53 |

5,000-row x 30-column sheet (`perf_5000x30.xlsx`): **4.05 s** end to end (status `needs_review`, 0 LLM calls).

Latency of LLM files includes retry back-off and `--sleep-between-llm` is *not* included. The first scanned file also pays the one-off OCR model load.

**Throughput** (no-LLM set: spreadsheets and bad files; re-run, wall clock; this is files/min, **not** pages/min):

| concurrency | files | seconds | files/min |
|---|---|---|---|
| 1 | 27 | 6.411 | 252.7 |
| 4 | 27 | 5.531 | 292.9 |

**Parser-stage-only timing** (generated large digital PDF; not end-to-end; no OCR, no LLM):

| pages | size MB | intake | pdf_triage | pdf_text | total s | pages/min |
|---|---|---|---|---|---|---|
| 220 | 0.3 | 0.015 | 0.382 | 83.221 | 83.618 | 157.9 |

### 10. Tier usage and LLM cost

| tier | invoices | share |
|---|---|---|
| ocr | 1 | 2.6% |
| parser | 16 | 42.1% |
| sheet | 20 | 52.6% |
| vision | 1 | 2.6% |

Vision escalation rate: 2.6% of invoices; files that ran `vision_extract`: 10. LLM calls per document (attempted, incl. failed): mean 0.63, p50 0.00, p95 2.00, total 29. Tokens: 17215 in / 15591 out (successful calls only).

### 11. Sheet efficiency

21 / 22 sheet layouts that reached `sheet_map` needed **0** LLM calls (95.5%). The mapping cache is disabled in this run, so this is the synonym pass alone.

### 12. Segmentation (multi-invoice PDFs)

| files | exact-match files | GT invoices | predicted invoices | correct boundaries | recall | precision |
|---|---|---|---|---|---|---|
| 1 | 1 | 3 | 3 | 3 | 100.0% | 100.0% |

A boundary is correct when a predicted invoice covers exactly the page set of a ground-truth invoice.

## 4. Error analysis (fill in the Cause column)

### Silent errors (1 invoices, 1 rows)

| file | invoice | kind | field | expected | got | issue codes | Cause |
|---|---|---|---|---|---|---|---|
| mixed.pdf | INV-MIX-42-0001 | wrong_value | line_items | 3 lines | lines tp=3 fp=0 fn=0 attrs_ok=False | - | Cause: ___ |

### Hallucinations (0; GT null, prediction non-null)

None in the files that ran.

## 5. What the numbers do not show

- **Synthetic data.** Layouts come from three generated templates plus degraded scans; real suppliers' layouts, stamps, handwriting and multilingual text are not covered. No handwriting at all; few currencies and date formats.
- **Small sample.** 73 ground-truth invoices in total, fewer in the subset that ran. Null-valued GT fields exist only for `subtotal` and `tax` (5 each) and `invoice_number` (2), so correct-null/hallucination rates rest on about a dozen cases.
- **Subset bias.** Only 46 of 93 files ran; 0 were skipped by LLM quota, 32 because `--no-llm` was set. Skipped files may be easier or harder than those that ran.
- **Ground-truth gaps.** Files whose GT lists no invoices (several images, sheet edge cases, triage files) are scored at file level only; any invoice extracted from them is an unscored *extra*, except that a `validated_ok` extra in a file expecting `validated_ok` counts as a silent error. `eval:false` triage files are excluded from field metrics but kept in status and latency tables.
- **Injected errors.** `wrong_total_*` ground truth stores the *printed* (wrong) total, so "fully correct" means "extracted what is printed"; detection is measured separately in section 3.6.
- **Duplicate check.** Each file runs with a fresh in-memory store, pre-seeded only for the injected duplicates, so cross-file duplicates among fixtures that share an invoice number are not flagged here as they would be in production. The sheet-mapping cache is off.
- **Nondeterminism.** The LLM is called at temperature 0 but is not deterministic; one run is one sample (see the run-to-run section if present).
- **In-process run.** API validation, queueing, DB constraints and real concurrency are outside these numbers.

## 6. Reproduce

```powershell
cd backend
python -m scripts.generate_fixtures --seed 42 --out data/fixtures
python -m scripts.evaluate --no-llm --out ../eval/reports/nollm-test
python -m scripts.evaluate --no-llm --resume ../eval/reports/nollm-test --throughput --parser-bench 220
python -m scripts.evaluate --resume ../eval/reports/nollm-test --groups injected,digital_pdf --max-llm-files 12 --sleep-between-llm 5
python -m scripts.evaluate --resume ../eval/reports/nollm-test --files scanned_moderate.pdf,scan_mild.png,mixed.pdf,blurry_severe.png,freeform_001.xlsx,xlsx_odd_headers.xlsx,multi_inv_001.pdf --max-llm-calls 9 --sleep-between-llm 3
python -m scripts.evaluate --resume ../eval/reports/nollm-test --files scanned_moderate.pdf --max-llm-calls 2
python -m scripts.evaluate --resume ../eval/reports/nollm-test --files scan_mild.png,mixed.pdf,blurry_severe.png,freeform_001.xlsx,xlsx_odd_headers.xlsx,multi_inv_001.pdf --max-llm-calls 8 --sleep-between-llm 3
python -m scripts.evaluate --resume ../eval/reports/nollm-test --files xlsx_odd_headers.xlsx --max-llm-calls 1
```
