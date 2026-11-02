# node: segment
**Purpose.** Split a document into invoice units so each LLM call sees exactly one invoice. Heuristics only (latency and cost).
**Reads** `page_text`, `tables`, `page_source`, `low_conf_pages`, `file_type`, `page_count`. **Writes** `units: list[{id, pages, text, tables, source, needs_vision, low_conf_pages}]`, `warnings`.
## Algorithm
- Image, single-page PDF, free-form sheet: one unit per page/sheet (a single image = one unit).
- Multi-page PDF/TIFF: per-page features:
  - `anchor`: regex over the first 15 non-empty lines: `(tax\s+)?invoice\s*(no|number|num|#)\.?\s*[:#]?\s*([A-Z0-9][\w\-/]{2,})` (also `bill no`, `receipt no`).
  - `pxy`: `page\s+(\d+)\s*(of|/)\s*(\d+)`.
  - `header_sig`: hash of the first 3 non-empty lines with digits removed.
- New unit starts at page `p` when: anchor present and differs from the current unit's anchor; or `pxy` says page 1 of N; or (no anchor, no `pxy`, `header_sig` equals the unit's first-page signature, and the previous page contained a totals marker: `total|amount due|balance due|grand total`).
- Continuation when `pxy` page > 1, or none of the above fire.
- No anchors found anywhere in a multi-page file -> **fallback: one unit per page**, issue `SEGMENT_FALLBACK` (info). `llm_extract` then merges adjacent units whose extracted `invoice_number` is equal and non-null (concatenate text, re-extract once).
- Unit `text` = concatenated `page_text[p]` values in order (plain text, no `=== PAGE ===` prefix); `source` = digital|ocr|mixed|sheet; `needs_vision` = any page in `low_conf_pages`.
**Known weakness.** Two invoices on one page, or unusual layouts with no anchors -> wrong split; `validate` catches symptoms, vision/manual review handles them.
## Tests
- T1 `single_page.pdf` -> 1 unit. T2 `three_page_one_invoice.pdf` (Page x of 3) -> 1 unit, pages [0,1,2].
- T3 `multi_invoice_10.pdf` (10 invoices, 1-3 pages) -> boundaries equal ground truth.
- T4 `no_anchor.pdf` -> per-page fallback + `SEGMENT_FALLBACK`. T5 `two_on_one_page.pdf` -> 1 unit (documented limitation; test asserts no crash).
- T6 low-conf page inside unit -> `needs_vision=True`. T7 freeform sheet with 2 sheets -> 2 units.
- T8 unit page lists are disjoint and cover all pages.
**Done when** T1-T8 pass; record boundary accuracy on multi-invoice fixtures.
