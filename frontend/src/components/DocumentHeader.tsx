"use client";

import { useState } from "react";
import { ApiError, reprocess } from "@/lib/api";
import { formatBytes, formatTime, show } from "@/lib/format";
import type { DocumentDetailOut } from "@/lib/types";
import { StatusChip } from "./StatusChip";
import { ProgressBar } from "./ProgressBar";

function KeyValues({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return <p className="muted small">—</p>;
  return (
    <dl className="kv">
      {entries.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{show(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DocumentHeader({ doc, onChanged }: { doc: DocumentDetailOut; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const canReprocess = doc.status === "failed" || doc.status === "needs_review";

  async function run() {
    const force = doc.status === "needs_review";
    if (force && !window.confirm("This document is in needs_review. Reprocessing replaces the current extraction. Continue?")) {
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await reprocess(doc.id, force);
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Reprocess failed");
    } finally {
      setBusy(false);
    }
  }

  const tier = doc.tier_summary ? Object.entries(doc.tier_summary).map(([k, v]) => `${k}: ${show(v)}`).join(" · ") : "";

  return (
    <section className="card" aria-label="Document">
      <div className="doc-title">
        <h1>{doc.filename}</h1>
        <StatusChip status={doc.status} />
      </div>
      <div className="meta">
        {doc.file_type && <span>{doc.file_type}</span>}
        {doc.size_bytes !== null && <span>{formatBytes(doc.size_bytes)}</span>}
        {doc.page_count !== null && <span>{doc.page_count} page(s)</span>}
        <span>attempt {doc.attempt}</span>
        <span>created {formatTime(doc.created_at)}</span>
        {doc.finished_at && <span>finished {formatTime(doc.finished_at)}</span>}
        {tier && <span>tiers — {tier}</span>}
      </div>

      {(doc.status === "queued" || doc.status === "processing") && (
        <div style={{ marginTop: 8, maxWidth: 260 }}>
          <ProgressBar status={doc.status} node={doc.current_node} />
        </div>
      )}

      {doc.status === "failed" && (
        <div className="failbox" role="alert">
          <strong>{doc.error_code ?? "FAILED"}</strong>
          {doc.error ?? "Processing failed."}
        </div>
      )}

      <div className="actions">
        <button type="button" className="btn btn-primary" onClick={run} disabled={!canReprocess || busy}>
          {busy ? "Reprocessing…" : "Reprocess"}
        </button>
        {!canReprocess && <span className="muted small">Available for failed or needs_review documents</span>}
        {err && (
          <span className="err-text small" role="alert">
            {err}
          </span>
        )}
      </div>

      <details>
        <summary>Timings, LLM usage, pipeline events</summary>
        <h4>Timings</h4>
        <KeyValues data={doc.timings ?? {}} />
        <h4>LLM usage</h4>
        <KeyValues data={doc.llm_usage ?? {}} />
        <h4>Events</h4>
        {doc.events.length === 0 ? (
          <p className="muted small">—</p>
        ) : (
          <ul className="small">
            {doc.events.map((e) => (
              <li key={e.id}>
                #{e.attempt} {e.node} {e.seconds !== null ? `${e.seconds.toFixed(2)}s` : ""}{" "}
                {e.ok === false ? <span className="err-text">failed</span> : ""}
              </li>
            ))}
          </ul>
        )}
      </details>
    </section>
  );
}
