"use client";

import { useEffect, useState } from "react";
import { fileUrl, getPages, isAbort } from "@/lib/api";
import { extensionOf } from "@/lib/limits";
import type { PageOut } from "@/lib/types";

type Tab = "original" | "text";

function Original({ id, filename }: { id: string; filename: string }) {
  const ext = extensionOf(filename);
  const url = fileUrl(id);
  if (ext === ".pdf") return <iframe className="viewer-frame" src={url} title={`Original: ${filename}`} />;
  if ([".png", ".jpg", ".jpeg"].includes(ext)) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img className="viewer-img" src={url} alt={`Original: ${filename}`} />;
  }
  return (
    <p className="muted">
      {ext === ".tif" || ext === ".tiff"
        ? "Browsers cannot preview TIFF images."
        : "Spreadsheets have no preview."}{" "}
      <a href={url} download>
        Download the original
      </a>
      .
    </p>
  );
}

function ExtractedText({ id }: { id: string }) {
  const [pages, setPages] = useState<PageOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    getPages(id, ac.signal)
      .then(setPages)
      .catch((e) => {
        if (!isAbort(e)) setError(e instanceof Error ? e.message : "Failed to load text");
      });
    return () => ac.abort();
  }, [id]);

  if (error) return <div className="alert" role="alert">{error}</div>;
  if (!pages) return <p className="muted">Loading text…</p>;
  if (pages.length === 0) return <p className="muted">No extracted text stored.</p>;
  return (
    <div>
      {pages.map((p) => (
        <div key={p.page_no} style={{ marginBottom: 12 }}>
          <div className="small">
            <strong>Page {p.page_no}</strong>{" "}
            {p.source && <span className="chip">{p.source}</span>}{" "}
            {p.ocr_conf !== null && <span className="chip">OCR conf {p.ocr_conf.toFixed(2)}</span>}
          </div>
          <div className="page-text">{p.text ?? ""}</div>
        </div>
      ))}
    </div>
  );
}

export function SourceViewer({ id, filename }: { id: string; filename: string }) {
  const [tab, setTab] = useState<Tab>("original");
  return (
    <section className="card" aria-label="Source">
      <div className="subtabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "original"} onClick={() => setTab("original")}>
          Original
        </button>
        <button type="button" role="tab" aria-selected={tab === "text"} onClick={() => setTab("text")}>
          Extracted text
        </button>
      </div>
      {tab === "original" ? <Original id={id} filename={filename} /> : <ExtractedText id={id} />}
    </section>
  );
}
