// Pure helpers for the parts list (BOM) page (R5): money formatting, the per-line total, grouping
// made and bought parts, and the vendor-total label. No DOM, no network.
import type { Bom, BomLine, VendorTotal } from "../../../api/browser";

const SYM: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", JPY: "¥", CAD: "$", AUD: "$" };

/** "$24.50", "1,250.00 SEK" — a money amount with its currency symbol, or the code when unknown. */
export function money(amount: number, currency = "USD"): string {
  const code = currency.toUpperCase();
  const frac = code === "JPY" ? 0 : 2;
  const n = amount.toLocaleString(undefined, { minimumFractionDigits: frac, maximumFractionDigits: frac });
  return SYM[code] ? `${SYM[code]}${n}` : `${n} ${code}`;
}

/** A line's total, or null when the cost is unknown (no unit cost yet). */
export function lineTotal(line: BomLine): number | null {
  return line.unit_cost === null ? null : line.unit_cost * line.qty;
}

/** The made parts, then the bought parts; each group keeps the core's order. */
export function grouped(lines: readonly BomLine[]): { made: BomLine[]; bought: BomLine[] } {
  return { made: lines.filter((l) => l.kind === "made"), bought: lines.filter((l) => l.kind !== "made") };
}

/** "ServoCity", "Made in-house", "No vendor" for the vendor breakdown. */
export function vendorLabel(v: VendorTotal | { vendor: string | null }): string {
  return v.vendor ?? "No vendor";
}

/** True when any line's cost is an estimate (made parts), so the page can show one "estimate" note. */
export function hasEstimate(bom: Bom): boolean {
  return bom.totals.estimated || bom.lines.some((l) => l.estimated);
}

/** The key specs as one short line ("KV 1750 · mass 27.5 g"), capped. */
export function specsLine(line: BomLine, max = 4): string {
  return line.specs.slice(0, max).map((s) => `${s.key} ${s.value}${s.unit ? ` ${s.unit}` : ""}`).join(" · ");
}
