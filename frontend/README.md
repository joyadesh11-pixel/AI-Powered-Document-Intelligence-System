# Frontend

Next.js (App Router) + TypeScript, plain CSS, no component or state library.

## Run
```powershell
# backend side: docker compose up -d; uvicorn app.api.main:app --reload; celery worker (see CLAUDE.md)
cd frontend
npm install
npm run dev          # http://localhost:3000
```
`/api/*` is rewritten to `http://localhost:8000/api/*` (`next.config.js`; set `API_ORIGIN` to change the target). No CORS needed in dev.

Checks: `npx tsc --noEmit` and `npm run build`.

## Layout
- `src/lib/types.ts` mirrors `backend/app/api/schemas.py` (money is `string | null`).
- `src/lib/api.ts` typed client; the `{error:{code,message,fields?}}` envelope is parsed once into `ApiError`.
- `src/lib/usePoll.ts` 2 s polling on a timeout chain (no overlapping requests), aborted and cancelled on key change/unmount, pauses while the tab is hidden, stops when `continueWhile` returns false, `refresh()` resumes.
- `src/lib/decimal.ts` display-only exact sum of line amounts (BigInt on scaled integers). No `parseFloat`/`Number`/`parseInt` on amounts anywhere; the server parses everything.

## Where the real API differs from the docs
- `InvoiceOut` has no `lines_sum`/`derived`/`raw`: "Sum of line amounts" is computed in the browser for display only.
- Per-file upload errors come back inside the 202 body (`UploadRow.error`), not as the error envelope.
- `DocumentDetailOut.issues` holds document-level issues only; invoice issues are in `invoices[].issues`.
- List rows carry `current_node` but no events/timings, so the list progress bar is 4 coarse stages.
- PATCH merges line items by `position`; lines cannot be added or removed from the UI.
- Issue `field` indexes line items by list index; PATCH 422 errors use the line `position`.

## API key
Dev runs with `API_KEY` unset. If the API requires `X-API-Key`, the browser must not hold the key; add a server-side proxy (Next route handler) that injects the header, and route `/file` through it too, since `<iframe>`/`<img>` cannot send headers. `NEXT_PUBLIC_API_KEY` exists for local testing only.

## Out of scope
Auth UI, i18n, bulk edit, websocket push, visual bounding boxes, adding/removing line items, search filters beyond supplier and status.
