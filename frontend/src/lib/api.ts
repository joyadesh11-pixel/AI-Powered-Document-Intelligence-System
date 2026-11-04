import type {
  DocumentDetailOut,
  DocumentRowOut,
  InvoiceOut,
  Page,
  PageOut,
  PatchInvoice,
  SearchMode,
  SearchPageOut,
  UploadRow,
} from "./types";

// "/api" goes through the Next rewrite (no CORS). Override to call the API directly.
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api";
// Local testing only: a key in NEXT_PUBLIC_* ships in the browser bundle. See README.
const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

export class ApiError extends Error {
  status: number;
  code: string;
  fields?: Record<string, string>;
  constructor(status: number, code: string, message: string, fields?: Record<string, string>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

export function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

// The one place the {error:{code,message,fields?}} envelope is handled.
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (API_KEY) headers.set("X-API-Key", API_KEY);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch (e) {
    if (isAbort(e)) throw e;
    throw new ApiError(0, "NETWORK", "Cannot reach the server");
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string; fields?: Record<string, string> } } | null)
      ?.error;
    throw new ApiError(
      res.status,
      err?.code ?? `HTTP_${res.status}`,
      err?.message ?? `Request failed (${res.status})`,
      err?.fields,
    );
  }
  return body as T;
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function uploadFiles(files: File[]): Promise<UploadRow[]> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  return request<UploadRow[]>("/documents", { method: "POST", body: form });
}

export function listDocuments(
  p: { status?: string; q?: string; limit?: number; offset?: number },
  signal?: AbortSignal,
): Promise<Page<DocumentRowOut>> {
  return request(`/documents${qs(p)}`, { signal });
}

export function getDocument(id: string, signal?: AbortSignal): Promise<DocumentDetailOut> {
  return request(`/documents/${id}`, { signal });
}

export function getPages(id: string, signal?: AbortSignal): Promise<PageOut[]> {
  return request(`/documents/${id}/pages`, { signal });
}

export function reprocess(id: string, force: boolean): Promise<UploadRow> {
  return request(`/documents/${id}/reprocess${qs({ force: force ? "true" : undefined })}`, {
    method: "POST",
  });
}

export function patchInvoice(id: string, body: PatchInvoice): Promise<InvoiceOut> {
  return request(`/invoices/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function search(
  p: { q: string; mode: SearchMode; supplier?: string; status?: string; limit?: number; offset?: number },
  signal?: AbortSignal,
): Promise<SearchPageOut> {
  return request(`/search${qs(p)}`, { signal });
}

export function fileUrl(id: string): string {
  return `${BASE}/documents/${id}/file`;
}
