# 07. Frontend (deliberately small)

Next.js (App Router) + TypeScript. Plain CSS or Tailwind, no component library, no state library.
`next.config.js` rewrites `/api/*` to `http://localhost:8000/api/*` (no CORS in dev). Data via `fetch` + a 2 s polling hook (`usePoll`). Priority: correctness of flows over polish.

## Screens
**1. Home (`/`)**: tabs *Upload & Jobs* | *Search*.
- `UploadZone`: drag-and-drop or picker, multi-file, client-side pre-check (extension, size) with the same limits as the API; shows per-file result from the 202 response (accepted / cached / rejected with reason).
- `JobTable`: rows = documents: filename, type, status chip (queued, processing, validated_ok, needs_review, failed), current node + small progress bar, invoices count, issue count, time. Filters: status, text filter. Row click -> detail.
- `SearchBar` + `SearchResults`: query box, mode toggle (keyword / semantic / hybrid), filters (supplier, status, currency, date range, total range); result cards with snippet and badge for `matched_by`; click -> detail.

**2. Document detail (`/documents/[id]`)**
- Header: filename, status, tier summary, timings, **Reprocess** button (enabled for `failed`; for `needs_review` behind a confirm).
- Left pane: source viewer. Tabs *Original* (PDF/image preview via `/file`; sheets show a note) and *Extracted text* (per page with `source` and OCR confidence badges).
- Right pane: for each invoice, `InvoiceCard`:
  - `ValidationPanel`: issues grouped by severity, each with message, expected vs got; clicking an issue focuses the field (and page/row if known).
  - `EditForm`: header fields as inputs (all strings; blank = null), line items as an editable table, derived `lines_sum` shown read-only. Fields with error issues get a red ring, `uncertain_fields` an amber ring.
  - **Save & revalidate** -> `PATCH /invoices/{id}`; on success update card (status, issues) and show "corrected" badge; on 422 show field errors inline.
- Failed documents show `error_code` and message prominently, plus Reprocess.

## Components
`UploadZone, JobTable, StatusChip, ProgressBar, SearchBar, SearchResults, DocumentHeader, SourceViewer, InvoiceCard, ValidationPanel, EditForm, LineItemsTable` + `lib/api.ts` (typed client) + `lib/types.ts` (mirror of API models, money as strings).

## Behaviours to get right
- Money stays a string end to end; no `parseFloat` on amounts (use input `inputMode="decimal"` and let the server parse).
- Polling stops when all visible documents are terminal; resumes on upload.
- Empty/loading/error states for every list. Disable Save while saving.
- Accessible labels on inputs; keyboard-friendly table.

## Out of scope (say so in README)
Auth UI, i18n, bulk edit, pagination beyond simple next/prev, websocket push, visual bounding boxes.

## Done when
Upload 3 mixed files -> see them progress to final statuses -> open a `needs_review` invoice -> fix the total -> it becomes `validated_ok` with "corrected" badge -> search finds it by supplier and by a semantic phrase. Verify manually and record this exact flow in the walkthrough video.
