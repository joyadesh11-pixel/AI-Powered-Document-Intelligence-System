# node: vision_extract (tier 3)
**Purpose.** Re-read units that the cheaper tiers could not handle, from page images, once.
**Reads** `vision_queue`, `units`, `invoices`, `page_images`, `file_path`. **Writes** `invoices` (tier `vision`), `escalated=True`, `vision_queue=[]`, `page_text`/`page_source` for pages it transcribed, `warnings`, `llm_usage`.
## Algorithm
1. For each queued unit (thread pool): obtain page images: reuse `page_images` (images, scanned PDF pages) or render digital PDF pages with `fitz` at `VISION_RENDER_DPI`; convert to JPEG, long side <= `VISION_MAX_SIDE_PX`. At most `VISION_MAX_PAGES` pages per call (more -> `VISION_PAGES_CAPPED`).
2. `llm.extract(VisionExtraction, SYSTEM_VISION_V1, user, images=..., model=LLM_MODEL_VISION)`; same retry policy.
3. Normalise like `llm_extract`. Replace stored page text with `source_text` for pages that were low-conf OCR (`page_source="vision"`), so search has real text.
4. **Arbitration** when a text-tier candidate exists: validate the vision candidate; keep the one with fewer error-severity issues; tie -> vision. The loser goes to `invoice.alternatives`.
5. Never runs for sheets. Never runs twice (`escalated`).
**Failure.** Vision call fails after retries -> keep text-tier invoice (if any) and add `VISION_FAILED` (error), or emit an all-null invoice stub with `VISION_FAILED` + `EXTRACTION_FAILED`; ends `needs_review`. Does not raise (except `LLMConfigError`).
## Tests (FakeLLM with `images` recording)
- T1 queue [1] -> only unit 1 re-extracted; others untouched. T2 digital PDF page rendered on demand (image bytes are valid JPEG, size <= limit).
- T3 vision fixes arithmetic -> vision kept, alternatives has the text candidate. T4 vision worse -> text kept.
- T5 `VISION_MAX_PAGES` cap -> warning. T6 failure -> `VISION_FAILED`, `needs_review`, no exception.
- T7 `escalated` is True afterwards and `vision_queue` empty. T8 `source_text` replaces low-conf OCR text.
- T9 `@live` blurry fixture -> fields match ground truth (record result).
**Done when** T1-T8 pass; T9 run once.
