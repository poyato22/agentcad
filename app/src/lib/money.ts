// Money as the parts list and part cards show it. A currency the page or user never gave is
// "unknown" (null, or the core totals' "unknown" key): it is never shown as, or assumed to be,
// dollars. Amounts are never converted or added across currencies.

const SYMBOL: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", JPY: "¥" };

/** "$24.50", "€2.40", "1,250.00 SEK", "24.50 (currency unknown)". */
export function money(amount: number, currency: string | null | undefined): string {
  const code = currency && /^[A-Z]{3}$/.test(currency) ? currency : null;
  const frac = code === "JPY" ? 0 : 2;
  const n = amount.toLocaleString("en-US", { minimumFractionDigits: frac, maximumFractionDigits: frac });
  if (!code) return `${n} (currency unknown)`;
  return SYMBOL[code] ? `${SYMBOL[code]}${n}` : `${n} ${code}`;
}

/** The label for a currency key ("USD", or "Currency unknown" for null / "unknown"). */
export function currencyLabel(currency: string | null | undefined): string {
  return currency && /^[A-Z]{3}$/.test(currency) ? currency : "Currency unknown";
}
