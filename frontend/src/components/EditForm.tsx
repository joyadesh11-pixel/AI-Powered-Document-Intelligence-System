"use client";

import { useMemo, useState } from "react";
import { ApiError, patchInvoice } from "@/lib/api";
import { sumDecimalStrings } from "@/lib/decimal";
import {
  DATE_FIELDS,
  FIELD_LABELS,
  LINE_COLS,
  MONEY_FIELDS,
  inputId,
  issueKey,
  lineKey,
  patchErrorKey,
  type LineCol,
} from "@/lib/fields";
import { HEADER_FIELDS, type HeaderField, type InvoiceOut, type LineItemPatch } from "@/lib/types";
import { LineItemsTable } from "./LineItemsTable";

type HeaderValues = Record<HeaderField, string>;
type LineValues = Record<number, Record<LineCol, string>>;

function headerFrom(inv: InvoiceOut): HeaderValues {
  const out = {} as HeaderValues;
  for (const f of HEADER_FIELDS) out[f] = inv[f] ?? "";
  return out;
}

function linesFrom(inv: InvoiceOut): LineValues {
  const out: LineValues = {};
  for (const li of inv.line_items) {
    out[li.position] = {
      description: li.description ?? "",
      quantity: li.quantity ?? "",
      unit_price: li.unit_price ?? "",
      amount: li.amount ?? "",
    };
  }
  return out;
}

const orNull = (v: string): string | null => (v.trim() === "" ? null : v);

interface Props {
  invoice: InvoiceOut;
  onSaved: (updated: InvoiceOut) => void;
}

export function EditForm({ invoice, onSaved }: Props) {
  const base = useMemo(() => headerFrom(invoice), [invoice]);
  const baseLines = useMemo(() => linesFrom(invoice), [invoice]);
  const [values, setValues] = useState<HeaderValues>(base);
  const [lines, setLines] = useState<LineValues>(baseLines);
  const [saving, setSaving] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  const errorKeys = useMemo(() => {
    const s = new Set<string>();
    for (const i of invoice.issues) {
      if (i.severity !== "error") continue;
      const k = issueKey(i.field, invoice.line_items);
      if (k) s.add(k);
    }
    return s;
  }, [invoice]);

  const uncertainKeys = useMemo(() => {
    const s = new Set<string>();
    for (const f of invoice.uncertain_fields ?? []) {
      const k = issueKey(f, invoice.line_items);
      if (k) s.add(k);
    }
    return s;
  }, [invoice]);

  const dirty = useMemo(() => {
    if (HEADER_FIELDS.some((f) => values[f] !== base[f])) return true;
    return invoice.line_items.some((li) =>
      LINE_COLS.some((c) => (lines[li.position]?.[c] ?? "") !== (baseLines[li.position]?.[c] ?? "")),
    );
  }, [values, lines, base, baseLines, invoice.line_items]);

  const linesSum = useMemo(
    () => sumDecimalStrings(invoice.line_items.map((li) => lines[li.position]?.amount ?? "")),
    [lines, invoice.line_items],
  );

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (saving || !dirty) return;
    const fields: Record<string, string | null> = {};
    for (const f of HEADER_FIELDS) if (values[f] !== base[f]) fields[f] = orNull(values[f]);
    const line_items: LineItemPatch[] = [];
    for (const li of invoice.line_items) {
      const patch: { position: number } & Partial<Record<LineCol, string | null>> = { position: li.position };
      let any = false;
      for (const c of LINE_COLS) {
        const now = lines[li.position]?.[c] ?? "";
        if (now !== (baseLines[li.position]?.[c] ?? "")) {
          patch[c] = orNull(now);
          any = true;
        }
      }
      if (any) line_items.push(patch);
    }
    setSaving(true);
    setFieldErrors({});
    setFormError(null);
    try {
      const updated = await patchInvoice(invoice.id, {
        ...(Object.keys(fields).length > 0 ? { fields } : {}),
        ...(line_items.length > 0 ? { line_items } : {}),
      });
      onSaved(updated);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422 && err.fields) {
        const mapped: Record<string, string> = {};
        const rest: string[] = [];
        for (const [k, msg] of Object.entries(err.fields)) {
          const key = patchErrorKey(k);
          if (key) mapped[key] = msg;
          else rest.push(`${k}: ${msg}`);
        }
        setFieldErrors(mapped);
        setFormError(rest.length > 0 ? rest.join("; ") : "Some values could not be parsed. Fix the highlighted fields.");
      } else {
        setFormError(err instanceof ApiError ? err.message : "Save failed");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} aria-label="Edit invoice" noValidate>
      <div className="form-grid">
        {HEADER_FIELDS.map((f) => {
          const err = fieldErrors[f];
          const cls = errorKeys.has(f) || err ? "ring-error" : uncertainKeys.has(f) ? "ring-uncertain" : "";
          const id = inputId(invoice.id, f);
          return (
            <div key={f}>
              <label htmlFor={id}>
                <span className="label-text">{FIELD_LABELS[f]}</span>
              </label>
              <input
                id={id}
                className={cls}
                type="text"
                inputMode={MONEY_FIELDS.includes(f) ? "decimal" : "text"}
                placeholder={DATE_FIELDS.includes(f) ? "YYYY-MM-DD" : undefined}
                autoComplete="off"
                aria-invalid={cls === "ring-error"}
                aria-describedby={err ? `${id}-err` : undefined}
                value={values[f]}
                disabled={saving}
                onChange={(e) => setValues((v) => ({ ...v, [f]: e.target.value }))}
              />
              {err && (
                <div id={`${id}-err`} className="field-error">
                  {err}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <h4>Line items</h4>
      <LineItemsTable
        invoiceId={invoice.id}
        lines={invoice.line_items}
        values={lines}
        onChange={(pos, col, v) => setLines((l) => ({ ...l, [pos]: { ...l[pos], [col]: v } }))}
        errorKeys={errorKeys}
        uncertainKeys={uncertainKeys}
        fieldErrors={fieldErrors}
        disabled={saving}
      />
      <p className="sum-row">
        <span className="muted">Sum of line amounts (computed here, read-only): </span>
        <strong>{linesSum ?? "—"}</strong>
      </p>

      {formError && (
        <div className="alert" role="alert">
          {formError}
        </div>
      )}
      <div className="save-row">
        <button type="submit" className="btn btn-primary" disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save & revalidate"}
        </button>
        {!dirty && !saving && <span className="muted small">No changes</span>}
      </div>
    </form>
  );
}
