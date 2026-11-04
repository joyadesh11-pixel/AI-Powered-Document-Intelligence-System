"use client";

import { useEffect, useState } from "react";
import { listDocuments } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { isTerminal } from "@/lib/types";
import { UploadZone } from "@/components/UploadZone";
import { JobTable } from "@/components/JobTable";
import { SearchBar } from "@/components/SearchBar";

const PAGE_SIZE = 50;

function JobsTab() {
  const [status, setStatus] = useState("");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => {
      setQ(qInput.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const { data, error, loading, refresh } = usePoll(
    (signal) => listDocuments({ status, q, limit: PAGE_SIZE, offset }, signal),
    {
      key: JSON.stringify({ status, q, offset }),
      // stop when every visible row is terminal; refresh() (upload) resumes
      continueWhile: (page) => page.items.some((d) => !isTerminal(d.status)),
    },
  );

  return (
    <>
      <UploadZone onUploaded={refresh} />
      <JobTable
        data={data}
        loading={loading}
        error={error}
        onRetry={refresh}
        status={status}
        q={qInput}
        onStatus={(v) => {
          setStatus(v);
          setOffset(0);
        }}
        onQ={setQInput}
        onOffset={setOffset}
      />
    </>
  );
}

export default function Home() {
  const [tab, setTab] = useState<"jobs" | "search">("jobs");
  return (
    <>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "jobs"} onClick={() => setTab("jobs")}>
          Upload &amp; Jobs
        </button>
        <button type="button" role="tab" aria-selected={tab === "search"} onClick={() => setTab("search")}>
          Search
        </button>
      </div>
      <div hidden={tab !== "jobs"}>
        <JobsTab />
      </div>
      <div hidden={tab !== "search"}>
        <SearchBar />
      </div>
    </>
  );
}
