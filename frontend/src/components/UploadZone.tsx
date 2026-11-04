"use client";

import { useRef, useState } from "react";
import { ApiError, uploadFiles } from "@/lib/api";
import { ALLOWED_EXTENSIONS, MAX_FILES_PER_REQUEST, MAX_FILE_MB, precheck } from "@/lib/limits";

interface FileResult {
  name: string;
  kind: "accepted" | "cached" | "rejected";
  detail?: string;
}

export function UploadZone({ onUploaded }: { onUploaded: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [results, setResults] = useState<FileResult[]>([]);

  async function handleFiles(list: FileList | File[]) {
    const files = Array.from(list);
    if (files.length === 0) return;
    setBusy(true);
    const out: FileResult[] = [];
    const sendable: File[] = [];
    for (const f of files) {
      const reason = precheck(f);
      if (reason) out.push({ name: f.name, kind: "rejected", detail: reason });
      else sendable.push(f);
    }
    for (let i = 0; i < sendable.length; i += MAX_FILES_PER_REQUEST) {
      const chunk = sendable.slice(i, i + MAX_FILES_PER_REQUEST);
      try {
        const rows = await uploadFiles(chunk);
        rows.forEach((row, idx) => {
          const name = row.filename || chunk[idx]?.name || "file";
          if (row.error) out.push({ name, kind: "rejected", detail: row.error.message });
          else if (row.cached) out.push({ name, kind: "cached", detail: "already processed" });
          else out.push({ name, kind: "accepted", detail: row.status ?? undefined });
        });
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : "Upload failed";
        for (const f of chunk) out.push({ name: f.name, kind: "rejected", detail: msg });
      }
    }
    setResults(out);
    setBusy(false);
    onUploaded();
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <section className="card" aria-label="Upload documents">
      <div
        className={over ? "dropzone dropzone-over" : "dropzone"}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          void handleFiles(e.dataTransfer.files);
        }}
      >
        <p>Drag files here, or</p>
        <input
          ref={inputRef}
          id="file-input"
          className="sr-only"
          type="file"
          multiple
          accept={ALLOWED_EXTENSIONS.join(",")}
          onChange={(e) => e.target.files && void handleFiles(e.target.files)}
        />
        <label htmlFor="file-input" className={busy ? "btn btn-disabled" : "btn btn-primary"}>
          {busy ? "Uploading…" : "Choose files"}
        </label>
        <p className="muted small">
          PDF, JPG, PNG, TIFF, XLSX, XLS, CSV · up to {MAX_FILE_MB} MB each · {MAX_FILES_PER_REQUEST} per batch
        </p>
      </div>
      {results.length > 0 && (
        <ul className="upload-results" aria-live="polite">
          {results.map((r, i) => (
            <li key={`${r.name}-${i}`}>
              <span className={`chip chip-up-${r.kind}`}>{r.kind}</span> {r.name}
              {r.detail ? <span className="muted"> — {r.detail}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
