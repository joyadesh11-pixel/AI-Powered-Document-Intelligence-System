import type { IssueOut } from "@/lib/types";
import { show } from "@/lib/format";

const ORDER = ["error", "warning", "info"];

interface Props {
  issues: IssueOut[];
  /** When set, issues with a field become buttons that focus that field. */
  onFocus?: (issue: IssueOut) => void;
  focusable?: (issue: IssueOut) => boolean;
}

export function ValidationPanel({ issues, onFocus, focusable }: Props) {
  if (issues.length === 0) return <p className="muted small">No validation issues.</p>;
  const groups = ORDER.map((sev) => ({ sev, items: issues.filter((i) => i.severity === sev) })).filter(
    (g) => g.items.length > 0,
  );
  const known = new Set(ORDER);
  const other = issues.filter((i) => !known.has(i.severity));
  if (other.length > 0) groups.push({ sev: "other", items: other });

  return (
    <div>
      {groups.map((g) => (
        <section key={g.sev} aria-label={`${g.sev} issues`}>
          <h4 className="small muted">
            {g.sev} ({g.items.length})
          </h4>
          <ul className="issues">
            {g.items.map((i) => {
              const body = (
                <>
                  <span className={`chip chip-sev-${i.severity}`}>{i.code}</span>
                  {i.field && <span className="small muted"> {i.field}</span>}
                  <div className="issue-msg">{i.message}</div>
                  {(i.expected !== null || i.got !== null) && (
                    <div className="small">
                      expected <strong>{i.expected ?? "—"}</strong> · got <strong>{i.got ?? "—"}</strong>
                    </div>
                  )}
                  {i.location && Object.keys(i.location).length > 0 && (
                    <div className="small muted">
                      at{" "}
                      {Object.entries(i.location)
                        .map(([k, v]) => `${k} ${show(v)}`)
                        .join(", ")}
                    </div>
                  )}
                </>
              );
              const canFocus = onFocus && i.field && (!focusable || focusable(i));
              return (
                <li key={i.id} className={`sev-${i.severity}`}>
                  {canFocus ? (
                    <button type="button" className="issue-btn" onClick={() => onFocus(i)}>
                      {body}
                    </button>
                  ) : (
                    body
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
