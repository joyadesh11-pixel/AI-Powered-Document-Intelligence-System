# node: validate
**Purpose.** Deterministic second line of defence against wrong values. Plain Python, `Decimal`, no LLM. Rules are catalogued in `docs/04-validation.md`; this node orchestrates them.
**Reads** `invoices`, `page_text`, `page_source`, `deps.seen_store`, `escalated`, `file_type`. **Writes** `invoices[*].issues`, `invoices[*].status`, `validation_errors` (flat list with invoice index), `vision_queue`.
## Algorithm
1. For each invoice run rules in order: completeness, formats/dates/currency, arithmetic, grounding, duplicates.
2. Duplicates: key = `casefold(supplier) | normalised(invoice_number) | total`; skip if any part is null. Lookup `seen_store.contains(key, exclude_hash=file_hash)` (read-only; the **caller** adds after persisting) so a reprocessed document never matches itself. Also compare within the same file (second occurrence flagged).
3. Invoice status = `needs_review` if any error-severity issue, else `validated_ok`.
4. **Escalation set:** units whose invoices have an issue in `ESCALATION_CODES` (docs/04) AND `tier in {parser, ocr}` AND `file_type != "sheet"` AND `not escalated`. Written to `vision_queue`. `route_after_validate` goes to `vision_extract` iff the queue is non-empty and `not escalated`.
5. Second pass (after vision): same rules; no new queue entries.
**Failure.** Rules never raise on bad data (a null field means the rule is skipped or reports missing). Unexpected exception = bug -> wrapper.
## Tests (one per rule + orchestration; see docs/04 for the table)
- T1-T12 one test per rule code with a minimal invoice (fails/passes).
- T13 `Decimal("0.1")+Decimal("0.2")==Decimal("0.3")` style check proves no float use (arith with such values passes).
- T14 tolerance edges: diff 0.01 passes, 0.02 fails.
- T15 sheet invoice with `ARITH_TOTAL` -> `needs_review` and **empty** `vision_queue`.
- T16 escalated flag set -> no new queue entries.
- T17 non-escalation codes only (`DUPLICATE_INVOICE`) -> empty queue.
- T18 within-file duplicate flagged on the second invoice only.
- T19 grounding: digital = error, ocr = warning, vision = skipped.
**Done when** T1-T19 pass; every issue code in docs/02 is produced by at least one test.
