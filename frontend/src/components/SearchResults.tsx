import Link from "next/link";
import type { SearchResultOut } from "@/lib/types";
import { StatusChip } from "./StatusChip";

// ts_headline may wrap hits in <b>…</b>. Render them as <mark> without any HTML injection.
function Snippet({ text }: { text: string }) {
  const parts = text.split(/(<b>.*?<\/b>)/gs);
  return (
    <p className="result-snippet">
      {parts.map((p, i) =>
        p.startsWith("<b>") && p.endsWith("</b>") ? (
          <mark key={i}>{p.slice(3, -4)}</mark>
        ) : (
          <span key={i}>{p.replace(/<\/?b>/g, "")}</span>
        ),
      )}
    </p>
  );
}

export function SearchResults({ items }: { items: SearchResultOut[] }) {
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {items.map((r, i) => (
        <li key={`${r.document_id}-${r.invoice_id ?? "p"}-${i}`}>
          <Link href={`/documents/${r.document_id}`} className="result">
            <div className="result-head">
              <strong>{r.supplier_name ?? r.filename ?? "Unknown supplier"}</strong>
              {r.invoice_number && <span>#{r.invoice_number}</span>}
              {r.status && <StatusChip status={r.status} />}
              <span className="chip">{r.matched_by}</span>
              <span className="chip">{r.kind}</span>
            </div>
            <div className="small muted">
              {r.filename}
              {r.invoice_date ? ` · ${r.invoice_date}` : ""}
              {r.total !== null ? ` · ${r.total}${r.currency ? " " + r.currency : ""}` : ""}
            </div>
            {r.snippet && <Snippet text={r.snippet} />}
          </Link>
        </li>
      ))}
    </ul>
  );
}
