# node: finalize
**Purpose.** Produce the final, consistent `PipelineResult`. **Always runs** (even after errors; `skip_on_error=False`).
**Reads** everything. **Writes** `status`, final `invoices`, `timings`, `warnings`.
## Algorithm
1. If `error_code` set -> `failed`.
2. Else if `parse_ok is False` (mapping exhausted) -> add `MAPPING_FAILED`, `needs_review`.
3. Per invoice: `validated_ok` iff zero error-severity issues. Document status: no invoices -> `needs_review` + `NO_INVOICE_DETECTED`; any invoice `needs_review` -> `needs_review`; else `validated_ok`.
4. Timings: `total` = sum of node seconds; `by_node` aggregates history; `tier_used` per invoice.
5. Strip non-serialisable/heavy state (`frames`, `page_images`). Include `page_text`, `page_source`, `ocr_conf`, `llm_usage`, `pipeline_version`.
6. Idempotent: running twice yields the same result.
## Tests
- T1 state matrix -> status: (error) failed; (no error, invoices all clean) validated_ok; (one invoice error) needs_review; (no invoices) needs_review + `NO_INVOICE_DETECTED`; (`parse_ok=False`) needs_review.
- T2 result is valid `PipelineResult` and JSON round-trips. T3 invariant: `validated_ok` => no error issues.
- T4 `frames` absent from result. T5 `timings.by_node` sums to `total` within 1 ms.
- T6 after an injected exception in a middle node -> `failed` with `INTERNAL_ERROR` and history showing the node.
**Done when** T1-T6 pass and all 12 end-to-end scenarios still end in the expected status.
