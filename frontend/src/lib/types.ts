// Mirrors backend/app/api/schemas.py. Money is a string (or null) end to end.

export type Money = string | null;

export type DocStatus = "queued" | "processing" | "validated_ok" | "needs_review" | "failed";
export type Severity = "error" | "warning" | "info";
export type SearchMode = "keyword" | "semantic" | "hybrid";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ErrorBody {
  code: string;
  message: string;
  fields?: Record<string, string>;
}

// POST /documents -> 202 [UploadRow]. Per-file errors are nested here, not in the envelope.
export interface UploadRow {
  document_id: string | null;
  filename: string;
  status: string | null;
  cached: boolean;
  error: { code: string; message: string } | null;
}

export interface IssueOut {
  id: string;
  invoice_id: string | null;
  code: string;
  severity: string;
  field: string | null;
  expected: string | null;
  got: string | null;
  message: string;
  location: Record<string, unknown> | null;
  resolved: boolean;
}

export interface LineItemOut {
  position: number;
  description: string | null;
  quantity: Money;
  unit_price: Money;
  amount: Money;
}

export interface CorrectionOut {
  field: string;
  old_value: string | null;
  new_value: string | null;
  at: string;
}

export interface EventOut {
  id: number;
  attempt: number;
  node: string;
  seconds: number | null;
  ok: boolean | null;
  meta: Record<string, unknown> | null;
  at: string;
}

export interface InvoiceOut {
  id: string;
  document_id: string;
  unit_index: number | null;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  supplier_name: string | null;
  customer_name: string | null;
  subtotal: Money;
  tax: Money;
  total: Money;
  currency: string | null;
  status: string;
  corrected: boolean;
  tier: string | null;
  uncertain_fields: string[] | null;
  source_pages: number[] | null;
  source_rows: number[] | null;
  dup_of: string | null;
  created_at: string;
  updated_at: string;
  line_items: LineItemOut[];
  issues: IssueOut[];
  corrections: CorrectionOut[];
}

export interface DocumentRowOut {
  id: string;
  filename: string;
  file_type: string | null;
  size_bytes: number | null;
  page_count: number | null;
  status: string;
  current_node: string | null;
  attempt: number;
  error_code: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  invoice_count: number;
  issue_count: number;
  error_count: number;
}

export interface DocumentDetailOut {
  id: string;
  filename: string;
  file_type: string | null;
  size_bytes: number | null;
  page_count: number | null;
  status: string;
  current_node: string | null;
  attempt: number;
  error_code: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  tier_summary: Record<string, unknown> | null;
  timings: Record<string, unknown> | null;
  llm_usage: Record<string, unknown> | null;
  events: EventOut[];
  invoices: InvoiceOut[];
  issues: IssueOut[]; // document-level issues only; invoice issues are inside invoices[]
}

export interface PageOut {
  page_no: number;
  text: string | null;
  source: string | null;
  ocr_conf: number | null;
}

export interface SearchResultOut {
  document_id: string;
  invoice_id: string | null;
  kind: string;
  filename: string | null;
  supplier_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  total: Money;
  currency: string | null;
  status: string | null;
  snippet: string | null;
  score: number;
  matched_by: string;
}

export interface SearchPageOut extends Page<SearchResultOut> {
  degraded: boolean;
}

export interface LineItemPatch {
  position: number;
  description?: string | null;
  quantity?: string | null;
  unit_price?: string | null;
  amount?: string | null;
}

export interface PatchInvoice {
  fields?: Record<string, string | null>;
  line_items?: LineItemPatch[];
}

export const HEADER_FIELDS = [
  "invoice_number",
  "invoice_date",
  "due_date",
  "supplier_name",
  "customer_name",
  "currency",
  "subtotal",
  "tax",
  "total",
] as const;
export type HeaderField = (typeof HEADER_FIELDS)[number];

export function isTerminal(status: string): boolean {
  return status !== "queued" && status !== "processing";
}
