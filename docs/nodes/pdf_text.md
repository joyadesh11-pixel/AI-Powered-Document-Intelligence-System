# node: pdf_text
**Purpose.** Extract exact text and tables from digital pages.
**Reads** `file_path`, `digital_pages`. **Writes** `page_text[p]`, `page_source[p]="digital"`, `tables[p]: list[str]` (markdown), `warnings`.
## Algorithm
1. `page.get_text("text", sort=True)` for reading order.
2. `page.find_tables()` -> each table to markdown (header row preserved).
3. `page_text[p]` = plain `sort=True` text only. No `=== PAGE N ===` prefix, no embedded tables.
**Fallbacks.** If `find_tables()` finds nothing but the page "looks tabular" (>= 6 lines with >= 2 numeric tokens) -> pdfplumber `extract_tables()`. Still nothing -> no tables; the LLM reads the plain text. If a page yields < `PAGE_MIN_CHARS` after all this, move it to `scanned_pages` (it will go through OCR).
**Failure.** Per-page exceptions -> page moves to `scanned_pages`; never fails the document.
**Edge.** Do not truncate here; `llm_extract` enforces `MAX_CONTEXT_CHARS`.
## Tests
- T1 `digital_clean.pdf` -> text contains known invoice number and total; markdown table has all line rows.
- T2 `borderless_table.pdf` -> pdfplumber fallback recovers rows (>= 80% of lines).
- T3 `mixed.pdf` -> only digital pages populated; scanned page absent from `page_text`.
- T4 page with empty text layer -> moved to `scanned_pages`.
- T5 reading order on two-column fixture.
**Done when** T1-T5 pass and a clean fixture's line-item count matches ground truth.
