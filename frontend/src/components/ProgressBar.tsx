import { isTerminal } from "@/lib/types";

const STAGES = ["intake", "extract", "validate", "finalize"];

function stageOf(node: string | null): number {
  switch (node) {
    case "intake":
      return 0;
    case "validate":
    case "vision_extract":
      return 2;
    case "finalize":
    case "fail":
      return 3;
    case null:
      return -1;
    default:
      return 1; // pdf_*, image_prep, ocr, sheet_*, segment, llm_extract
  }
}

export function ProgressBar({ status, node }: { status: string; node: string | null }) {
  const done = isTerminal(status);
  const current = done ? STAGES.length : stageOf(node);
  return (
    <div className="progress" title={node ?? status}>
      <div className="progress-segs" aria-hidden="true">
        {STAGES.map((s, i) => (
          <span key={s} className={i < current ? "seg seg-done" : i === current ? "seg seg-now" : "seg"} />
        ))}
      </div>
      <span className="progress-node">{done ? "" : (node ?? "waiting")}</span>
    </div>
  );
}
