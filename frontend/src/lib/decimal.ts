// Display-only exact decimal sum on strings (BigInt on scaled integers).
// No floating point and no number parsing; nothing computed here is ever sent to the server.

const DECIMAL = /^([+-])?(\d+)(?:\.(\d+))?$/;

interface Dec {
  units: bigint;
  scale: number;
}

function parse(s: string): Dec | null {
  const m = DECIMAL.exec(s.trim());
  if (!m) return null;
  const frac = m[3] ?? "";
  const units = BigInt(m[2] + frac);
  return { units: m[1] === "-" ? -units : units, scale: frac.length };
}

/** Sum of the non-blank strings, or null when any non-blank value is not a plain decimal. */
export function sumDecimalStrings(values: string[]): string | null {
  const parsed: Dec[] = [];
  for (const v of values) {
    if (v.trim() === "") continue;
    const d = parse(v);
    if (!d) return null;
    parsed.push(d);
  }
  if (parsed.length === 0) return null;
  const scale = parsed.reduce((a, d) => (d.scale > a ? d.scale : a), 0);
  let total = BigInt(0);
  for (const d of parsed) total += d.units * BigInt(10) ** BigInt(scale - d.scale);
  const neg = total < BigInt(0);
  const digits = (neg ? -total : total).toString().padStart(scale + 1, "0");
  const whole = digits.slice(0, digits.length - scale);
  const frac = digits.slice(digits.length - scale);
  return `${neg ? "-" : ""}${whole}${scale > 0 ? "." + frac : ""}`;
}
