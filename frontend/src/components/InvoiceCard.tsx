"use client";

import { inputId, issueKey } from "@/lib/fields";
import { formatTime, show } from "@/lib/format";
import type { InvoiceOut, IssueOut } from "@/lib/types";
import { EditForm } from "./EditForm";
import { StatusChip } from "./StatusChip";
import { ValidationPanel } from "./ValidationPanel";

interface Props {
  invoice: InvoiceOut;
  index: number;
  onSaved: (updated: InvoiceOut) => void;
}

export function InvoiceCard({ invoice, index, onSaved }: Props) {
  const targetOf = (i: IssueOut) => issueKey(i.field, invoice.line_items);

  function focus(i: IssueOut) {
    const key = targetOf(i);
    if (!key) return;
    const el = document.getElementById(inputId(invoice.id, key));
    if (el) {
      el.scrollIntoView({ block: "center" });
      el.focus();
    }
  }

  // re-initialise the form whenever the stored invoice changes (after a save)
  const formKey = `${invoice.id}:${invoice.corrections.length}:${invoice.updated_at}`;

  return (
    <section className="card" aria-label={`Invoice ${index + 1}`}>
      <div className="invoice-head">
        <h3>Invoice {index + 1}</h3>
        <StatusChip status={invoice.status} />
        {invoice.corrected && <span className="chip chip-corrected">corrected</span>}
        {invoice.tier && <span className="chip">{invoice.tier}</span>}
        {invoice.dup_of && <span className="chip chip-needs_review">duplicate of {invoice.dup_of.slice(0, 8)}</span>}
      </div>
      <p className="small muted">
        {invoice.source_pages && invoice.source_pages.length > 0 && <>pages {invoice.source_pages.join(", ")} · </>}
        {invoice.source_rows && invoice.source_rows.length > 0 && <>rows {invoice.source_rows.join(", ")} · </>}
        updated {formatTime(invoice.updated_at)}
      </p>

      <h4>Validation</h4>
      <ValidationPanel issues={invoice.issues} onFocus={focus} focusable={(i) => targetOf(i) !== null} />

      <h4>Edit</h4>
      <EditForm key={formKey} invoice={invoice} onSaved={onSaved} />

      {invoice.corrections.length > 0 && (
        <details>
          <summary>Corrections ({invoice.corrections.length})</summary>
          <ul className="small">
            {invoice.corrections.map((c, i) => (
              <li key={`${c.field}-${c.at}-${i}`}>
                {c.field}: {show(c.old_value) || "∅"} → {show(c.new_value) || "∅"}{" "}
                <span className="muted">({formatTime(c.at)})</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
