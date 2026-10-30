// Pure helpers for the parts list (BOM) page (R5): per-currency totals (never added across
// currencies), line totals, made/bought grouping, vendor labels, quantity rules. No DOM.
import { UNKNOWN_CURRENCY, type Bom, type BomRow, type ByCurrency, type VendorTotal } from "../../../api/browser";
import { money } from "../../../lib/money";

/** A totals key as a currency for money(): the core's "unknown" key is no currency. */
export const keyCurrency = (key: string): string | null => (key === UNKNOWN_CURRENCY ? null : key);

/** The currencies a totals map holds, known codes first (alphabetical), "unknown" last. */
export function currencies(m: ByCurrency): string[] {
  return Object.keys(m).sort((a, b) => (a === UNKNOWN_CURRENCY ? 1 : b === UNKNOWN_CURRENCY ? -1 : a.localeCompare(b)));
}

/** "$35.97 · €2.40 · 3.00 (currency unknown)": one amount per currency, never summed. */
export function perCurrency(m: ByCurrency): string {
  const keys = currencies(m);
  return keys.length ? keys.map((k) => money(m[k], keyCurrency(k))).join(" · ") : "—";
}

/** A row's total as text ("—" when it has no price). */
export function lineTotalText(row: BomRow): string {
  return row.line_total === null ? "—" : money(row.line_total, row.currency);
}

/** Made parts, then bought parts; each group keeps the core's order. */
export function grouped(items: readonly BomRow[]): { made: BomRow[]; bought: BomRow[] } {
  return { made: items.filter((l) => l.kind === "made"), bought: items.filter((l) => l.kind === "bought") };
}

/** "Adafruit", "Made in-house", "No vendor". */
export function vendorLabel(v: Pick<VendorTotal, "vendor" | "kind">): string {
  return v.vendor ?? (v.kind === "made" ? "Made in-house" : "No vendor");
}

/** The key specs as one short line ("KV 1750 · mass 27.5 g"), capped. */
export function specsLine(row: BomRow, max = 4): string {
  return row.specs.slice(0, max).map((s) => `${s.key} ${s.value}${s.unit ? ` ${s.unit}` : ""}`).join(" · ");
}

/** A quantity the core accepts: a whole number from 1 to 100,000, else null. */
export function validQty(raw: string): number | null {
  const t = raw.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n >= 1 && n <= 100_000 ? n : null;
}

/** A currency code typed by the user: 3 letters (upper-cased), "" → null (unknown), else undefined (invalid). */
export function parseCurrency(raw: string): string | null | undefined {
  const t = raw.trim().toUpperCase();
  if (t === "") return null;
  return /^[A-Z]{3}$/.test(t) ? t : undefined;
}

/** The note under the totals when some rows carry no price. */
export function unpricedText(bom: Bom): string | null {
  const n = bom.totals.unpriced;
  if (n === 0) return null;
  return n === 1 ? "1 part has no price yet, so it isn't in the totals." : `${n} parts have no price yet, so they aren't in the totals.`;
}
