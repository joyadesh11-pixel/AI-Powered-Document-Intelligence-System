# 01. Architecture

## Goal
Automate what staff do by hand today: read invoices in mixed formats, extract fields,
check the maths, flag problems, keep everything searchable. Volume: about 1,000 pages/day.

## Layers (one-way dependencies)
```
frontend ──HTTP──> api ──> services ──> pipeline   (pure: file in, result out)
                              │  └────> db         (SQLAlchemy, pgvector)
workers (Celery) ──> services
```
- `pipeline/` knows nothing about DB, HTTP, queues. Input: a file path + `Deps`. Output: `PipelineResult`.
- `services/` is the only code that touches both pipeline and DB: ingest, persist results,
  embeddings, search, corrections, reprocess.
- `api/` validates input and calls services. `workers/` run `services.process_document(id)`.
- Progress: services iterate `stream_process()` and write a `processing_events` row per node.

## The graph (see `reference/graph_skeleton/graph.png`, regenerate with `draw_graph.py`)
```
START → intake ─┬─ pdf ──► pdf_triage ► pdf_text ─┬─ scanned/garbled pages ─► image_prep ─┐
                ├─ image ─────────────────────────┴─────────────────────────► image_prep ┤
                │                                                       image_prep ► ocr ┤
                ├─ sheet ─► sheet_load ► sheet_classify ─┬─ tabular ─► sheet_map ⇄ sheet_transform ─► validate
                │                                        └─ free-form ─────────────────┐
                └─ corrupt/unsupported ─► fail ─► END                                  ▼
   (pdf_text with no scanned pages ──────────────────────────────────────────────► segment)
   ocr ► segment ► llm_extract ► validate ─┬─ errors & not yet escalated & not a sheet ─► vision_extract ─► validate
                                           └─ otherwise ─► finalize ─► END
```
Mixed PDFs run sequentially: `pdf_text` (digital pages) then `image_prep`+`ocr` (scanned pages),
merged back in page order before `segment`.

## Tier model
| Tier | Input | Method | Used for |
|---|---|---|---|
| parser | digital PDF pages, spreadsheets | PyMuPDF text/tables, pandas | the majority; exact, free, ms |
| ocr | scans, images, TIFF frames, garbled pages | RapidOCR (Tesseract fallback) then LLM on text | decent-quality scans |
| vision | low-confidence OCR, blurry images, failed extraction, failed validation | vision LLM on page images, returns fields + `source_text` | the fallback; max once per unit |

Escalation signals: page text length, OCR mean confidence, image quality score,
`llm_extract` exhaustion, and `validate` errors in `ESCALATION_CODES` (docs/04).
One escalation maximum; after that a human reviews.

## Data flow per file type
| Input | Acquisition | Context for LLM | Extraction | Notes |
|---|---|---|---|---|
| Digital PDF | `pdf_triage` labels pages, `pdf_text` gets text + `find_tables()` markdown | per-unit text+tables | `llm_extract` | pdfplumber fallback for tables |
| Scanned PDF / image / TIFF | `image_prep` renders 250 DPI, `ocr` rebuilds rows by y-position | per-unit OCR text | `llm_extract`; poor pages go to vision | TIFF frames = pages |
| XLSX/XLS/CSV tabular | `sheet_load` (dtype=str) | column names + 10 sample rows only | `sheet_map` (1 LLM call or none) then pandas `sheet_transform` | LLM never sees rows |
| XLSX/CSV free-form | `sheet_load` renders grid as text | text | `llm_extract` | small files only |
| Corrupt/unsupported | `intake` | none | none | `failed` with `error_code` |

## State keys (graph state; JSON-serialisable except `frames`)
`file_path, file_type, file_hash, size_bytes, page_count, error_code, error` ·
`page_labels, digital_pages, scanned_pages, page_text, page_source, page_images, ocr_conf, low_conf_pages, tables` ·
`sheets, frames, sheet_kind, primary_sheet, mapping, mapping_feedback, mapping_retries, parse_ok` ·
`units, vision_queue, escalated, invoices, validation_errors, warnings, llm_usage` ·
`status, history`. History entry: `{node, seconds, ok, attempt, meta}`.

## Concurrency and performance
- Route per page; digital pages never touch OCR or an LLM-vision call.
- `ocr`: thread pool (`OCR_WORKERS`, onnxruntime releases the GIL). `llm_extract`: thread pool
  (`LLM_CONCURRENCY`) over units. Spreadsheets: vectorised pandas, no per-row Python where avoidable.
- Cache by `file_hash` (whole result) and by header-hash (sheet mappings).
- Celery workers scale horizontally; DB is the only shared state. Windows dev: `--pool=threads`.

## Decision log (one line each; the README "design decisions" section expands these)
| # | Decision | Why |
|---|---|---|
| D1 | LangGraph for orchestration | explicit graph, conditional edges, retry cycle, easy to diagram and test |
| D2 | Loader before LLM, vision as fallback | auditability, determinism on digits, cost/latency at volume, resilience to LLM outage, stored text powers search |
| D3 | LLM returns strings as printed; code parses to Decimal/date | enables grounding check, avoids float/locale errors, LLM does no maths |
| D4 | Sheets: LLM maps columns once, pandas does rows | cost constant per layout, numbers never retyped by a model |
| D5 | Escalate at most once | bounded cost/latency; humans handle the rest |
| D6 | Pipeline is pure, persistence outside | terminal-verifiable, testable, swappable queue/DB |
| D7 | PostgreSQL+pgvector | one DB for metadata, text, vectors; hybrid search in SQL |
| D8 | Celery+Redis | assignment-suggested, mature, horizontal scaling |
| D9 | Gemini (Flash text, Pro vision), configurable | one provider for text, vision, embeddings; names are env vars |
| D10 | RapidOCR first, Tesseract fallback | pip-only on Windows, ONNX speed |
| D11 | Docker Compose for infra only | reviewer can run in minutes; app runs locally |
| D12 | Optional API-key auth, upload limits, magic-byte validation | proportionate security for the scope; RBAC listed as future work |
| D13 | numpy + opencv-python-headless for deskew and blur scoring (M5) | pip-only on Windows; headless avoids GUI dependency; numpy already required by rapidocr |

## Scalability story (qualitative; measured numbers go in the eval report)
1,000 pages/day is about 2 pages/min over 8 hours, so one worker suffices; the design scales by
adding workers (stateless), raising `LLM_CONCURRENCY`, moving uploads to object storage,
partitioning `chunks` by date, and rate-limiting per API key. Do not claim numbers you did not measure.

## Reliability
Idempotent by hash; every node timed and wrapped; per-unit failure isolation; retries only on
retryable errors with backoff; failed jobs reprocessable; every record kept with its issues.

## Future work (list in README)
Cross-document verification (PDF-to-PDF, PDF-to-Excel), chunked extraction for huge single
invoices, discount/shipping/multi-tax schema, handwriting, multilingual tuning, RBAC/multi-tenant,
object storage, Alembic migrations, observability (OpenTelemetry), active-learning from corrections.
