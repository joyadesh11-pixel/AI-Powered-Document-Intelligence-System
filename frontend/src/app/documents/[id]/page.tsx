"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ApiError, getDocument } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { isTerminal, type InvoiceOut } from "@/lib/types";
import { DocumentHeader } from "@/components/DocumentHeader";
import { InvoiceCard } from "@/components/InvoiceCard";
import { SourceViewer } from "@/components/SourceViewer";
import { ValidationPanel } from "@/components/ValidationPanel";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const { data: doc, error, loading, refresh, mutate } = usePoll((s) => getDocument(id, s), {
    key: id,
    continueWhile: (d) => !isTerminal(d.status),
  });

  function saved(updated: InvoiceOut) {
    mutate((d) => (d ? { ...d, invoices: d.invoices.map((i) => (i.id === updated.id ? updated : i)) } : d));
    refresh(); // pulls the recomputed document status
  }

  return (
    <>
      <Link href="/" className="back">
        ← All documents
      </Link>

      {error && !doc && (
        <div className="alert" role="alert">
          {error instanceof ApiError && error.status === 404 ? "Document not found." : `Could not load document: ${error.message}`}{" "}
          <button type="button" className="btn" onClick={refresh}>
            Retry
          </button>
        </div>
      )}
      {loading && !doc && <p className="muted">Loading document…</p>}

      {doc && (
        <>
          {error && (
            <div className="notice" role="status">
              Live update failed: {error.message}
            </div>
          )}
          <DocumentHeader doc={doc} onChanged={refresh} />
          <div className="detail-grid">
            <SourceViewer id={doc.id} filename={doc.filename} />
            <div>
              {doc.issues.length > 0 && (
                <section className="card" aria-label="Document issues">
                  <h3>Document issues</h3>
                  <ValidationPanel issues={doc.issues} />
                </section>
              )}
              {doc.invoices.length === 0 ? (
                <p className="muted">
                  {isTerminal(doc.status) ? "No invoices were extracted from this document." : "Waiting for extraction…"}
                </p>
              ) : (
                doc.invoices.map((inv, i) => <InvoiceCard key={inv.id} invoice={inv} index={i} onSaved={saved} />)
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
