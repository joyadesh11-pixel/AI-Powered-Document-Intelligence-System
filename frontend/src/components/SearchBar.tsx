"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, isAbort, search } from "@/lib/api";
import type { SearchMode, SearchPageOut } from "@/lib/types";
import { SearchResults } from "./SearchResults";

const MODES: SearchMode[] = ["keyword", "semantic", "hybrid"];
const STATUSES = ["validated_ok", "needs_review", "failed"];
const LIMIT = 20;

interface Submitted {
  q: string;
  mode: SearchMode;
  supplier: string;
  status: string;
  offset: number;
}

export function SearchBar() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [supplier, setSupplier] = useState("");
  const [status, setStatus] = useState("");
  const [submitted, setSubmitted] = useState<Submitted | null>(null);
  const [page, setPage] = useState<SearchPageOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const acRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!submitted) return;
    acRef.current?.abort(); // a newer search supersedes an older one
    const ac = new AbortController();
    acRef.current = ac;
    setLoading(true);
    setError(null);
    search(
      {
        q: submitted.q,
        mode: submitted.mode,
        supplier: submitted.supplier.trim() || undefined,
        status: submitted.status || undefined,
        limit: LIMIT,
        offset: submitted.offset,
      },
      ac.signal,
    )
      .then((p) => {
        setPage(p);
        setLoading(false);
      })
      .catch((e) => {
        if (isAbort(e)) return;
        setError(e);
        setLoading(false);
      });
    return () => ac.abort();
  }, [submitted]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (q.trim() === "") return;
    setSubmitted({ q: q.trim(), mode, supplier, status, offset: 0 });
  }

  const offset = page?.offset ?? 0;
  const count = page?.items.length ?? 0;
  const unavailable = error instanceof ApiError && error.code === "EMBEDDING_UNAVAILABLE";

  return (
    <>
      <form className="card search-form" onSubmit={submit} role="search" aria-label="Search invoices">
        <label className="grow">
          <span className="label-text">Search</span>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="supplier, invoice number, or a phrase" />
        </label>
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="label-text">Mode</legend>
          <div className="mode-toggle">
            {MODES.map((m) => (
              <label key={m}>
                <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => setMode(m)} />
                {m}
              </label>
            ))}
          </div>
        </fieldset>
        <label>
          <span className="label-text">Supplier</span>
          <input type="text" value={supplier} onChange={(e) => setSupplier(e.target.value)} />
        </label>
        <label>
          <span className="label-text">Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn btn-primary" disabled={loading || q.trim() === ""}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {error && (
        <div className="alert" role="alert">
          {unavailable
            ? "Semantic search is unavailable right now. Try keyword mode."
            : `Search failed: ${error.message}`}
        </div>
      )}
      {page?.degraded && !error && (
        <div className="notice" role="status">
          Semantic search is degraded; showing keyword matches only.
        </div>
      )}

      {!submitted ? (
        <p className="muted">Enter a query to search extracted invoices and page text.</p>
      ) : loading && !page ? (
        <p className="muted">Searching…</p>
      ) : page && page.items.length === 0 && !error ? (
        <p className="muted">No results.</p>
      ) : page ? (
        <>
          <SearchResults items={page.items} />
          <div className="pager">
            <button
              type="button"
              className="btn"
              disabled={loading || offset === 0}
              onClick={() => submitted && setSubmitted({ ...submitted, offset: Math.max(0, offset - LIMIT) })}
            >
              Previous
            </button>
            <span className="small muted">
              {offset + 1}–{offset + count} of {page.total}
            </span>
            <button
              type="button"
              className="btn"
              disabled={loading || offset + count >= page.total}
              onClick={() => submitted && setSubmitted({ ...submitted, offset: offset + LIMIT })}
            >
              Next
            </button>
          </div>
        </>
      ) : null}
    </>
  );
}
