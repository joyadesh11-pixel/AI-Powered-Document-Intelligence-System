import { HEADER_FIELDS, type HeaderField, type LineItemOut } from "./types";

export type LineCol = "description" | "quantity" | "unit_price" | "amount";
export const LINE_COLS: LineCol[] = ["description", "quantity", "unit_price", "amount"];

export const FIELD_LABELS: Record<HeaderField, string> = {
  invoice_number: "Invoice number",
  invoice_date: "Invoice date",
  due_date: "Due date",
  supplier_name: "Supplier",
  customer_name: "Customer",
  currency: "Currency",
  subtotal: "Subtotal",
  tax: "Tax",
  total: "Total",
};

export const MONEY_FIELDS: HeaderField[] = ["subtotal", "tax", "total"];
export const DATE_FIELDS: HeaderField[] = ["invoice_date", "due_date"];

/** Stable key for an input: "total" for header fields, "li:<position>:<col>" for line cells. */
export function lineKey(position: number | string, col: string): string {
  return `li:${position}:${col}`;
}

export function inputId(invoiceId: string, key: string): string {
  return `inv-${invoiceId}-${key}`;
}

const LINE_REF = /^line_items\[(\d+)\]\.(\w+)$/;

/**
 * Maps a validation issue / uncertain-field name to an input key.
 * Issues index line items by list index (0-based); the key uses the line's position.
 * Index comparison is done on strings so no number parsing is involved.
 */
export function issueKey(field: string | null, lines: LineItemOut[]): string | null {
  if (!field) return null;
  if ((HEADER_FIELDS as readonly string[]).includes(field)) return field;
  const m = LINE_REF.exec(field);
  if (!m) return null;
  const line = lines.find((_, i) => String(i) === m[1]);
  return line ? lineKey(line.position, m[2]) : null;
}

/** Maps a PATCH 422 field key ("total", "line_items[2].amount" where 2 is a position). */
export function patchErrorKey(field: string): string | null {
  if ((HEADER_FIELDS as readonly string[]).includes(field)) return field;
  const m = LINE_REF.exec(field);
  return m ? lineKey(m[1], m[2]) : null;
}
