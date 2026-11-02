# 00. Skeleton review: fix these in M0 before writing real nodes

`reference/graph_skeleton/graph.py` proves the routing. These are the places where the
stub behaviour would be wrong or misleading once nodes are real. Each item says what to change.

## Must fix
1. **Vision escalation must never apply to spreadsheet invoices.**
   Stub: `validate` adds any failing invoice to `vision_queue`; `route_after_validate` then
   goes to `vision_extract`. A tabular sheet has no page images, so this is nonsense.
   Fix: `validate` only queues units where `tier in {"text","ocr"}` AND
   `file_type != "sheet"`. Sheet invoices that fail validation go straight to `finalize`
   as `needs_review`. Add a dry-run case: *xlsx with arithmetic error -> needs_review, no vision*.
2. **`vision_queue` mixes page ids and unit ids.**
   Stub `ocr` writes page numbers into `vision_queue`; `llm_extract` reads unit ids.
   Fix: `ocr` writes `low_conf_pages: list[int]`. `segment` maps pages to units and sets
   `unit["needs_vision"]=True` when any page is low-confidence. `llm_extract` skips those
   units and adds their ids to `vision_queue`. `vision_queue` holds **unit ids only**.
3. **Expected failures must not raise.**
   Stub wrapper turns any exception into `error` -> document `failed`. Real rule:
   - `llm_extract` exhausts retries on a unit -> put the unit in `vision_queue` + issue
     `EXTRACTION_FAILED`; do not raise.
   - `vision_extract` fails on a unit -> keep the text-tier invoice (if any), add
     `VISION_FAILED`, end as `needs_review`; do not raise.
   - Only `LLMConfigError` (bad key, quota exhausted) and genuine bugs fail the document.
4. **Vision result must not blindly replace the text result.**
   Stub replaces always. Real rule (arbitration): after vision + re-validate, keep the
   candidate with fewer error-severity issues; tie -> vision. Store both in
   `invoice.alternatives` for the audit trail.
5. **`finalize` is document-level only.** Needs per-invoice status; document status =
   worst of invoices (`failed` > `needs_review` > `validated_ok`). Zero invoices produced
   -> `needs_review` with `NO_INVOICE_DETECTED` (not `failed`).
6. **`process()` has no dependency injection.** Real signature:
   `process(file_path, *, deps: Deps | None = None) -> PipelineResult` and
   `stream_process(...)` (yields `(node_name, state_update)` using LangGraph
   `stream_mode="updates"`). `Deps` = `{config, llm, seen_store, mapping_cache}`.
   Pass via closure in `build_graph(deps)`, not through state (state must stay serialisable).
   The services layer uses `stream_process` to record progress without the graph knowing about a DB.
7. **`error` is a free string.** Make it `error_code` (enum) + `error` (message). See 02-schemas.
8. **`sim` switches must be removed** once real nodes exist (end of M7). Replace dry-run
   scenarios with real fixtures + `FakeLLM`. Keep the 12 scenarios as the integration test names.

## Should fix
9. State needs: `page_source: dict[int,str]` (digital|ocr|vision|sheet), `page_images`
   (paths, never bytes), `low_conf_pages`, `mapping_feedback`, `warnings`, `llm_usage`,
   `frames` (DataFrames; in-memory only, stripped by `finalize`), `error_code`.
10. History entries gain `attempt` and `meta` (e.g. `{"pages": 12}`) for the eval report.
11. `route_after_transform` uses `mapping_retries < 2` to mean "one retry". Add a comment
    and use `config.MAPPING_MAX_RETRIES = 1` explicitly.
12. File layout: split `graph.py` into `state.py`, `routers.py`, `nodes/*.py`, `graph.py`
    (mechanical move; behaviour unchanged; the 12 dry-run scenarios must still pass).
13. `fail` and `finalize` both set `status="failed"`. Keep both (the diagram is clearer),
    but `fail` must also build a valid `PipelineResult` with `error_code`.

## Known limitations (state them in the README, do not hide)
- Mixed workbooks (tabular + free-form sheets): the largest sheet decides the path; the
  others get `SHEET_SKIPPED`.
- Invoice discounts/shipping are outside the assignment schema, so such invoices flag
  `ARITH_TOTAL` for review.
- Segmentation is heuristic; two invoices on one page will mis-split.
- Table-heavy single invoices above `MAX_CONTEXT_CHARS` are flagged `CONTEXT_TRUNCATED`
  rather than chunk-extracted (stretch goal).
