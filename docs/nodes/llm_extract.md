# node: llm_extract
**Purpose.** Per unit, turn text into a typed invoice. Null for anything not printed.
**Reads** `units`, `vision_queue`, config. **Writes** `invoices` (tier `parser` or `ocr`), `vision_queue` (extended), `warnings`, `llm_usage`.
## Algorithm
1. Skip units with `needs_vision=True` (OCR too poor): add to `vision_queue` straight away. Skip units whose text is empty: same.
2. Context = unit text + tables, with `=== PAGE n (digital|ocr) ===` markers. If longer than `MAX_CONTEXT_CHARS` -> truncate, issue `CONTEXT_TRUNCATED` (error), continue (stretch: chunked extraction).
3. Call `llm.extract(ExtractedInvoice, SYSTEM_EXTRACT_V1, context, model=LLM_MODEL_TEXT)` in a thread pool (`LLM_CONCURRENCY`); preserve unit order in the output.
4. Retry per `docs/03` (schema errors with error text appended, transient with backoff). `LLMConfigError` propagates (document `failed`).
5. Normalise raw strings into `Invoice` via `utils/` (money, dates, currency). Unparseable value -> field `None`, raw kept in `invoice.raw`, issue `BAD_NUMBER`/`BAD_DATE`. Copy `uncertain_fields`; each becomes an `UNCERTAIN_FIELD` warning.
6. All header fields null and no lines -> `NO_INVOICE_DETECTED` (error) for that unit.
7. Retries exhausted for a unit -> `EXTRACTION_FAILED` issue + unit added to `vision_queue`; no invoice emitted from text tier.
8. If `SEGMENT_FALLBACK` was used: merge adjacent per-page invoices with equal non-null `invoice_number` (concatenate unit texts, re-extract once).
**Edge.** Prompt-injection inside the document text ("ignore previous instructions") must not change behaviour (prompt rule 9; test it).
## Tests (FakeLLM)
- T1 canned valid output -> typed invoice, `Decimal` amounts, `date` parsed, `tier=parser` (digital) / `ocr`.
- T2 missing invoice number: model returns null -> stays `None` (assert not `""`), later flagged `MISSING_REQUIRED` by validate.
- T3 first response schema-invalid, second valid -> 1 retry, retry prompt contains the error text.
- T4 always invalid -> unit in `vision_queue`, `EXTRACTION_FAILED`, no exception.
- T5 429 twice then success -> backoff called (patch sleep). T6 auth error -> `LLMConfigError` propagates.
- T7 `"1.234,56"` raw -> `Decimal("1234.56")`; `"abc"` -> `None` + `BAD_NUMBER`.
- T8 `needs_vision` unit makes zero LLM calls. T9 order preserved with concurrency 4.
- T10 injection text in unit -> prompt still contains the data-not-instructions rule; output unaffected (FakeLLM asserts prompt).
- T11 `@live` one real call on `digital_clean.pdf` -> header fields match ground truth.
**Done when** T1-T10 pass and T11 passes once manually.
