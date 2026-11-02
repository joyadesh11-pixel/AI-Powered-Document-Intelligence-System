# node: sheet_map
**Purpose.** Decide what each column means, once per sheet layout. The LLM sees column names and sample rows only, never the data.
**Reads** `sheets[primary]` (`preview`, `header_hash`), `mapping_feedback` (on retry). **Writes** `mapping: SheetMapping`, `llm_usage`.
## Algorithm
1. **Cache:** `deps.mapping_cache[header_hash]` hit and no feedback -> use it, no LLM.
2. **Deterministic pass** (skipped when `mapping_feedback` is set):
   - Normalise headers (casefold, strip punctuation/underscores, collapse spaces) and look up `pipeline/config/synonyms.py` (`inv no`, `invoice #`, `bill no` -> `invoice_number`; `vendor`, `supplier`, `seller` -> `supplier_name`; `line amt`, `amount`, `line total` -> `amount`; etc.).
   - Date format: try candidate formats on up to 200 sample values; pick the one parsing 100%. If both `%d/%m/%Y` and `%m/%d/%Y` parse everything (all days <= 12), mark **ambiguous**.
   - Decimal/thousands separators by pattern voting. Currency from a currency column, ISO code in a header like `Amount (USD)`, or symbol/ISO in samples; else null.
   - Grain: `invoice_number` duplicates across rows -> `line_item` (group_by = that column) else `invoice`.
3. If every required field (`invoice_number`, an amount column) resolved and nothing is ambiguous -> done, **no LLM call**.
4. Otherwise one LLM call (`LLM_MODEL_TEXT`, schema `SheetMapping`) with the preview, the already-resolved parts, and what is unresolved; on retry also the failure feedback ("column Inv Date: 41% parsed with %m/%d/%Y; failing: 25/11/2026"). Unknown column names fail Pydantic validation -> schema retry.
5. Store in `mapping_cache` (the service layer persists it).
**Fallbacks.** LLM exhausted -> no mapping; set `parse_ok=False`; router sends to `finalize` as `needs_review` + `MAPPING_FAILED`.
**Edge.** Multiple amount-like columns (net/gross): prefer the one named line total; list the others in `unmapped_columns`. Never map a column the LLM is unsure of; leave it unmapped.
## Tests (FakeLLM)
- T1 standard headers -> mapping with **zero LLM calls**. T2 odd headers (`Doc Ref`, `Supplier Co`) -> 1 call.
- T3 ambiguous date (all days <= 12) -> LLM asked, deterministic part kept. T4 cache hit -> 0 calls.
- T5 feedback retry -> prompt contains the feedback text. T6 LLM names a non-existent column -> schema retry, then success.
- T7 LLM always invalid -> `MAPPING_FAILED` path. T8 grain detection line_item vs invoice.
**Done when** T1-T8 pass; record how many fixture sheets needed 0 LLM calls (eval report).
