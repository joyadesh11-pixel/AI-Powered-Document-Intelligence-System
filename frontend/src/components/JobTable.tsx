"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { DocumentRowOut, Page } from "@/lib/types";
import { formatTime } from "@/lib/format";
import { StatusChip } from "./StatusChip";
import { ProgressBar } from "./ProgressBar";

interface Props {
  data: Page<DocumentRowOut> | null;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
  status: string;
  q: string;
  onStatus: (v: string) => void;
  onQ: (v: string) => void;
  onOffset: (v: number) => void;
}

const STATUSES = ["queued", "processing", "validated_ok", "needs_review", "failed"];

export function JobTable({ data, loading, error, onRetry, status, q, onStatus, onQ, onOffset }: Props) {
  const router = useRouter();
  const items = data?.items ?? [];
  const offset = data?.offset ?? 0;
  const total = data?.total ?? 0;

  return (
    <section className="card" aria-label="Documents">
      <div className="toolbar">
        <label>
          <span className="label-text">Status</span>
          <select value={status} onChange={(e) => onStatus(e.target.value)}>
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="grow">
          <span className="label-text">Filter by filename</span>
          <input type="search" value={q} onChange={(e) => onQ(e.target.value)} placeholder="e.g. invoice" />
        </label>
      </div>

      {error && (
        <div className="alert" role="alert">
          Could not load documents: {error.message}{" "}
          <button type="button" className="btn" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}

      {loading && !data ? (
        <p className="muted">Loading documents…</p>
      ) : items.length === 0 && !error ? (
        <p className="muted">No documents yet. Upload some files above.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Filename</th>
                <th>Type</th>
                <th>Status</th>
                <th>Progress</th>
                <th className="num">Invoices</th>
                <th className="num">Issues</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.id} className="row-link" onClick={() => router.push(`/documents/${d.id}`)}>
                  <td>
                    <Link href={`/documents/${d.id}`} onClick={(e) => e.stopPropagation()}>
                      {d.filename}
                    </Link>
                    {d.error_code && <div className="small err-text">{d.error_code}</div>}
                  </td>
                  <td>{d.file_type ?? ""}</td>
                  <td>
                    <StatusChip status={d.status} />
                  </td>
                  <td>
                    <ProgressBar status={d.status} node={d.current_node} />
                  </td>
                  <td className="num">{d.invoice_count}</td>
                  <td className="num">
                    {d.issue_count}
                    {d.error_count > 0 && <span className="err-text small"> ({d.error_count} errors)</span>}
                  </td>
                  <td className="small">{formatTime(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > 0 && (
        <div className="pager">
          <button type="button" className="btn" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - (data?.limit ?? 50)))}>
            Previous
          </button>
          <span className="small muted">
            {offset + 1}–{offset + items.length} of {total}
          </span>
          <button
            type="button"
            className="btn"
            disabled={offset + items.length >= total}
            onClick={() => onOffset(offset + (data?.limit ?? 50))}
          >
            Next
          </button>
        </div>
      )}
    </section>
  );
}
