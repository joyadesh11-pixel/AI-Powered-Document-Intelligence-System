# node: ocr
**Purpose.** Read text from page images, preserving row structure so line items stay on one line.
**Reads** `page_images`, `low_conf_pages`. **Writes** `page_text[p]`, `page_source[p]="ocr"`, `ocr_conf[p]` (mean, length-weighted), `low_conf_pages` (extended).
## Algorithm
1. For each page not already in `low_conf_pages` (thread pool, `OCR_WORKERS`): RapidOCR -> list of (box, text, confidence).
2. Rebuild lines: sort by y-centre; start a new line when `|yc - line_yc| > 0.5 * median_box_height`; within a line sort by x; join with a single space, or a tab if the horizontal gap > 1.5 x average char width (keeps table columns apart).
3. `ocr_conf[p]` = mean confidence weighted by text length. If `< OCR_CONF_MIN` or total chars < 20 -> add to `low_conf_pages`, issue `LOW_OCR_CONF` (warning). The text is still stored (search, audit) but the unit will go to vision.
4. Orientation: if confidence is low, retry once rotated 90 and 270 degrees; keep the best.
**Fallbacks.** RapidOCR exception -> pytesseract (`TESSERACT_CMD`) `image_to_data`. Both fail -> page into `low_conf_pages`, text empty.
**Failure.** Never fails the document. **Time cap:** if OCR tuning exceeds 45 minutes of build time, stop and route more pages to vision.
## Tests
- T1 `scan_clean.png` -> conf >= 0.9 and text contains the known invoice number and total.
- T2 line integrity: a known line item (description + qty + price + amount) appears on one output line.
- T3 `rotated_90.png` -> recovered via retry. T4 `blank.png` -> low_conf.
- T5 monkeypatch RapidOCR to raise -> Tesseract used (skip test if Tesseract absent).
- T6 both engines raise -> page in `low_conf_pages`, no exception.
- T7 pages in `low_conf_pages` from `image_prep` are not OCR'd. T8 4 pages processed in parallel keep page order.
**Done when** T1-T8 pass; record mean OCR conf + seconds/page on the scan fixtures for the eval report.
