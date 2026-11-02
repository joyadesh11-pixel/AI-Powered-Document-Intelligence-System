# node: pdf_triage
**Purpose.** Label every PDF page so each gets the cheapest tier that works.
**Reads** `file_path`, `page_count`. **Writes** `page_labels: dict[int,str]` (digital|scanned|garbled), `digital_pages`, `scanned_pages` (scanned + garbled).
## Algorithm (per page, PyMuPDF)
- `chars = len(page.get_text().strip())`.
- `img_cover` = sum of image bbox areas / page area (`page.get_image_info()`).
- `garbled_ratio` = share of characters that are `(cid:` fragments, U+FFFD, private-use, or control chars; also low "word-like token" ratio.
- Label: `chars < PAGE_MIN_CHARS` -> scanned · `garbled_ratio > GARBLED_RATIO_MAX` -> garbled · `img_cover > 0.85 and chars < 200` -> scanned (stray page number over a scan) · else digital.
**Known gap.** A scan with an invisible OCR text layer is labelled digital. Accepted: `validate` grounding/arithmetic plus vision escalation catch bad layers. Document it, don't special-case it.
**Failure.** A page that cannot be read -> label scanned (OCR will try). Never raises.
## Tests
- T1 `digital_clean.pdf` -> all digital. T2 `scanned.pdf` -> all scanned.
- T3 `mixed.pdf` (5 digital + 1 scanned) -> labels per page.
- T4 `garbled_cid.pdf` -> garbled. T5 `blank_page.pdf` -> scanned.
- T6 `scan_with_pagenum.pdf` -> scanned. T7 labels cover every page index exactly once.
**Done when** T1-T7 pass.
