import { LINE_COLS, inputId, lineKey, type LineCol } from "@/lib/fields";
import type { LineItemOut } from "@/lib/types";

const COL_LABEL: Record<LineCol, string> = {
  description: "Description",
  quantity: "Qty",
  unit_price: "Unit price",
  amount: "Amount",
};

interface Props {
  invoiceId: string;
  lines: LineItemOut[];
  values: Record<number, Record<LineCol, string>>;
  onChange: (position: number, col: LineCol, value: string) => void;
  errorKeys: Set<string>;
  uncertainKeys: Set<string>;
  fieldErrors: Record<string, string>;
  disabled: boolean;
}

export function LineItemsTable({
  invoiceId,
  lines,
  values,
  onChange,
  errorKeys,
  uncertainKeys,
  fieldErrors,
  disabled,
}: Props) {
  if (lines.length === 0) return <p className="muted small">No line items.</p>;
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">Line items</caption>
        <thead>
          <tr>
            <th scope="col">#</th>
            {LINE_COLS.map((c) => (
              <th key={c} scope="col">
                {COL_LABEL[c]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lines.map((li) => (
            <tr key={li.position}>
              <th scope="row">{li.position}</th>
              {LINE_COLS.map((col) => {
                const key = lineKey(li.position, col);
                const err = fieldErrors[key];
                const cls = errorKeys.has(key) || err ? "ring-error" : uncertainKeys.has(key) ? "ring-uncertain" : "";
                const money = col !== "description";
                return (
                  <td key={col}>
                    <input
                      id={inputId(invoiceId, key)}
                      className={`line-input ${cls}`}
                      type="text"
                      inputMode={money ? "decimal" : "text"}
                      autoComplete="off"
                      aria-label={`Line ${li.position} ${COL_LABEL[col]}`}
                      aria-invalid={cls === "ring-error"}
                      value={values[li.position]?.[col] ?? ""}
                      disabled={disabled}
                      onChange={(e) => onChange(li.position, col, e.target.value)}
                    />
                    {err && <div className="field-error">{err}</div>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
