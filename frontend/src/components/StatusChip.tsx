import { label } from "@/lib/format";

export function StatusChip({ status }: { status: string }) {
  return <span className={`chip chip-${status}`}>{label(status)}</span>;
}
