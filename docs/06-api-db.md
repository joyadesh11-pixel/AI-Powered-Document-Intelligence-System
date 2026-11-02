# 06. API, database, workers, search, security

Layers: `api/` (FastAPI routes, input validation) -> `services/` (only layer touching pipeline + DB) -> `db/` (SQLAlchemy 2.x models). `workers/` call `services.process_document(document_id)`.
No Alembic: `Base.metadata.create_all()` on startup; say so in the README.

## Database (PostgreSQL 16 + pgvector)
```sql
CREATE EXTENSION IF NOT EXISTS vector;

documents(
  id uuid PK, filename text, file_hash char(64) UNIQUE, file_type text, size_bytes bigint, page_count int,
  status text,             -- queued|processing|validated_ok|needs_review|failed
  error_code text, error text, current_node text, attempt int DEFAULT 1,
  tier_summary jsonb, timings jsonb, llm_usage jsonb, pipeline_version text,
  created_at timestamptz, updated_at timestamptz, finished_at timestamptz)

invoices(
  id uuid PK, document_id uuid FK ON DELETE CASCADE, unit_index int,
  invoice_number text, invoice_date date, due_date date, supplier_name text, customer_name text,
  subtotal numeric(18,4), tax numeric(18,4), total numeric(18,4), currency char(3),
  status text,             -- validated_ok|needs_review
  corrected boolean DEFAULT false, tier text, uncertain_fields jsonb, raw jsonb, extra jsonb, derived jsonb,
  source_pages int[], source_rows int[], dup_key text, alternatives jsonb, created_at, updated_at)
CREATE INDEX ON invoices(supplier_name), (invoice_date), (status), (document_id);
CREATE UNIQUE INDEX uq_invoice_dupkey ON invoices(dup_key) WHERE dup_key IS NOT NULL AND status <> 'failed';
-- NOTE: duplicates are *flagged* by validate, so the unique index applies only to the "accepted" copy:
--       services stores the flagged duplicate with dup_key = NULL and dup_of = <invoice id> (column below).
ALTER TABLE invoices ADD COLUMN dup_of uuid NULL REFERENCES invoices(id);

line_items(id uuid PK, invoice_id uuid FK ON DELETE CASCADE, position int,
  description text, quantity numeric(18,4), unit_price numeric(18,4), amount numeric(18,4))

validation_issues(id uuid PK, document_id uuid FK, invoice_id uuid FK NULL, code text, severity text,
  field text, expected text, got text, message text, location jsonb, resolved boolean DEFAULT false)

document_pages(id uuid PK, document_id uuid FK, page_no int, text text, source text, ocr_conf real)

chunks(id uuid PK, document_id uuid FK, invoice_id uuid FK NULL, kind text, -- 'page'|'invoice_summary'
  page_no int, text text, tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
  embedding vector(768), indexed boolean DEFAULT false)
CREATE INDEX ON chunks USING gin(tsv);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);

processing_events(id bigserial PK, document_id uuid FK, attempt int, node text, seconds real,
  ok boolean, meta jsonb, at timestamptz)

corrections(id bigserial PK, invoice_id uuid FK, field text, old_value text, new_value text, at timestamptz)
```
Money columns are `numeric`; never `float`. `invoice_summary` chunk text = supplier, number, date, total, currency, line descriptions; it is what makes "invoices from Acme in November" work semantically.

## Services
- `ingest.save_upload(file) -> Document`: validate (below), stream to `data/uploads/<sha256>`, compute hash, **if hash exists return the stored document** (cache; no reprocessing), else insert `queued` and enqueue.
- `processing.process_document(id)`: set `processing`; iterate `stream_process(path, deps)`; for each `(node, update)` write `processing_events` and `documents.current_node`; on result: persist document/pages/invoices/lines/issues in one transaction; compute `dup_key`/`dup_of`; call `seen_store` adapter backed by the DB; set final status; enqueue `index_document(id)`.
- `indexing.index_document(id)`: build chunks (pages ~1000 chars, 100 overlap; one summary chunk per invoice), embed in batches of 32, set `indexed=true`. Failure leaves `indexed=false`; a periodic task retries. Never changes document status.
- `corrections.apply(invoice_id, patch)`: apply edits (header fields and/or line items) in a transaction; **re-run pure validation only** (`pipeline.validation.run_all`, no LLM); replace that invoice's issues; set `corrected=true`; recompute invoice status and document status (worst-of); log each field change; rebuild its summary chunk (mark `indexed=false`).
- `reprocess.run(document_id, force=False)`: allowed for `failed` (and `needs_review` only with `force=true`); bumps `attempt`, status `queued`, enqueues; previous events kept.
- `search.query(...)`: below.

## Endpoints (`/api/...`)
| Method | Path | Notes |
|---|---|---|
| POST | `/documents` | multipart, field `files` (1..20). Per file: ext + magic check, size <= `MAX_FILE_MB`. Response 202: `[{document_id, filename, status, cached:boolean, error?}]` (per-file errors do not fail the batch) |
| GET | `/documents` | `?status=&q=&limit=50&offset=0&sort=created_at:desc`; each row has status, current_node, counts of invoices/issues |
| GET | `/documents/{id}` | document, `events[]` (progress), `invoices[]` with `line_items`, `issues` |
| GET | `/documents/{id}/pages` | stored source text per page with `source` and `ocr_conf` |
| GET | `/documents/{id}/file` | original file (`Content-Disposition: inline`, sanitised name) for preview |
| GET | `/invoices/{id}` | one invoice with lines/issues/corrections |
| PATCH | `/invoices/{id}` | body `{fields:{invoice_number?, ...}, line_items?:[{position, ...}]}`; strings in, server parses; returns revalidated invoice. 422 on unparseable values (field-level errors) |
| POST | `/documents/{id}/reprocess` | `?force=false`; 409 if status not eligible |
| GET | `/search` | `q`, `mode=keyword|semantic|hybrid` (default hybrid), filters `supplier, status, currency, date_from, date_to, min_total, max_total`, `limit, offset` |
| GET | `/health` | DB, Redis, queue depth |
Errors: JSON `{error:{code,message,fields?}}`. IDs are UUIDs. Pagination everywhere.

### Progress
Frontend polls `GET /documents?status=...` every 2 s while any row is `queued|processing`; `current_node` and `events` drive the progress bar (nodes seen / expected path).

## Search
- **Keyword:** `plainto_tsquery('simple', q)` on `chunks.tsv`, plus `ILIKE` on invoice number/supplier; filters join `invoices`.
- **Semantic:** embed `q` (same model, `RETRIEVAL_QUERY` task type), `ORDER BY embedding <=> :q LIMIT 50`, only `indexed` chunks.
- **Hybrid:** Reciprocal Rank Fusion (k=60) of the two lists; group by invoice/document; return snippet (`ts_headline`) and matched chunk kind.
- Results include `document_id, invoice_id?, supplier, number, date, total, status, snippet, score, matched_by`.

## Workers
Celery app `workers/celery_app.py`; tasks `process_document`, `index_document`, `retry_unindexed` (beat, every 5 min). Settings: `acks_late=True`, `task_reject_on_worker_lost=True`, `task_time_limit=600`, `soft_time_limit=540`, `max_retries=2` for transient infra errors only (not for pipeline results). Windows: `--pool=threads --concurrency=4` (or `--pool=solo` for debugging). `CELERY_TASK_ALWAYS_EAGER=1` in API tests.

## Security (all go in the README)
- Upload: allow-list by **magic bytes**, size cap, page cap, zip-bomb guard, max 20 files/request, stored under the hash (user filename never used in a path), served with sanitised name and `X-Content-Type-Options: nosniff`.
- Optional auth: if `API_KEY` env is set, every route requires header `X-API-Key` (constant-time compare); otherwise open for local review. RBAC/multi-tenant = future work.
- Secrets only via env; `.env` git-ignored; no keys in logs. Prompt-injection guard in prompts + treat document text as data.
- DB access only through SQLAlchemy parameters (no string-built SQL). CORS restricted to the frontend origin. Basic rate limit (`slowapi`, e.g. 60 req/min/IP) if time.
- Failure isolation: per-unit; LLM outage -> documents with parser-tier success still complete; others `needs_review`/`failed` and are reprocessable.
- PII note: invoices may contain personal data; mention retention/deletion as future work (`DELETE /documents/{id}`; implement if time).

## Tests (`tests/api/`)
Upload valid/oversized/wrong-type/renamed-exe/too-many-files; cache on same hash; status transitions; `GET` shapes; PATCH revalidation (fix total -> `validated_ok`, `corrected=true`, document status recomputed); PATCH bad value 422; reprocess eligibility 409; search keyword/semantic (fake embeddings)/hybrid + filters; auth on/off; progress events recorded in order; duplicate invoice across two documents flagged.
