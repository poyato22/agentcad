// Display formatting. Times are unix seconds (the core's float timestamps), shown in local time.

const pad = (n: number) => String(n).padStart(2, "0");

/** "14:03:09" */
export function fmtClock(ts: number): string {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** "14:03" */
export function fmtHM(ts: number): string {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Seconds → "42s", "2m 14s", "1h 02m". */
export function fmtElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${pad(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${pad(Math.floor((s % 3600) / 60))}m`;
}

/** "6.00 mm" */
export function fmtMm(v: number, digits = 2): string { return `${v.toFixed(digits)} mm`; }

/** "12.40 g" */
export function fmtGrams(v: number, digits = 2): string { return `${v.toFixed(digits)} g`; }

/** "40.00 × 20.00 × 6.00" */
export function fmtSize(size: readonly number[], digits = 2): string { return size.map((x) => x.toFixed(digits)).join(" × "); }

/** "$0.41" */
export function fmtUsd(v: number): string { return `$${v.toFixed(2)}`; }

/** 950 → "950 tok", 38400 → "38k tok", 1250000 → "1.3M tok" */
export function fmtTokens(n: number): string {
  if (n < 1000) return `${n} tok`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k tok`;
  return `${(n / 1_000_000).toFixed(1)}M tok`;
}

/** Signed number with a real minus sign: "+0.80 g", "−772 mm³". */
export function fmtSigned(v: number, digits: number, unit = ""): string {
  const s = Math.abs(v).toFixed(digits);
  return `${v < 0 ? "−" : "+"}${s}${unit}`;
}

/** First 8 hex chars of a hash. */
export function shortHash(h: string): string { return h.slice(0, 8); }
